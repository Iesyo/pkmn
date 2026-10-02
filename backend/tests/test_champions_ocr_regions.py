"""Spatial exclusion before OCR and before battle interpretation."""

import importlib.util
import unittest
from types import SimpleNamespace

from pkmn_vgc.champions_replay.detector import DetectorContext
from pkmn_vgc.champions_replay.ocr_detector import (
    ChampionsCatalog, ChampionsOcrDetector, ChampionsTextParser, OcrLine, RapidOcrEngine,
)
from pkmn_vgc.champions_replay.ocr_regions import (
    mask_text_areas, text_screen, useful_lines,
)
from pkmn_vgc.champions_replay.sources import FramePacket

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


def line(text, x, y, confidence=.99):
    return OcrLine(text, confidence, x, y, min(x + .2, 1), min(y + .04, 1))


class ChampionsTextRegionTests(unittest.TestCase):
    @staticmethod
    def pixel(image, x, y):
        return image[int(y * image.shape[0]), int(x * image.shape[1]), 0]

    @unittest.skipUnless(HAS_NUMPY, "requiere NumPy del detector OCR opcional")
    def test_pixels_outside_useful_areas_and_clocks_are_hidden_without_changing_original(self):
        import numpy as np
        for shape in ((270, 480, 3), (1080, 1920, 3)):
            original = np.full(shape, 255, dtype=np.uint8)
            masked = mask_text_areas(original, "battle")
            with self.subTest(shape=shape):
                self.assertEqual(masked.shape, original.shape)
                self.assertTrue((original == 255).all())
                for x, y in ((.45, .04), (.40, .12), (.40, .34), (.18, .82), (.80, .17)):
                    self.assertEqual(self.pixel(masked, x, y), 0, (x, y))
                for x, y in ((.08, .86), (.14, .93), (.62, .05), (.83, .05),
                             (.08, .38), (.80, .38), (.15, .72), (.60, .31)):
                    self.assertEqual(self.pixel(masked, x, y), 255, (x, y))

    @unittest.skipUnless(HAS_NUMPY, "requiere NumPy del detector OCR opcional")
    def test_preview_selection_counter_is_useful_in_the_clock_position(self):
        import numpy as np
        counter = line("3/4", .17, .83)
        self.assertEqual(useful_lines([counter], screen="preview"), (counter,))
        self.assertEqual(useful_lines([counter], screen="battle"), ())
        preview = mask_text_areas(np.full((1000, 1000, 3), 255, dtype=np.uint8), "preview")
        self.assertEqual(self.pixel(preview, .17, .83), 255)

    def test_known_actor_or_move_words_outside_narration_do_not_create_actions(self):
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Delphox",)),
            catalog=ChampionsCatalog(species=("Delphox",), moves=("Protect",)),
        )
        parser._active["p1a"] = "Delphox"
        parser._battle_open = True
        for x, y in ((.45, .04), (.4, .34), (.4, .50)):
            result = parser.parse((line("Delphox used Protect!", x, y),), timestamp_ms=500, source_frame=1)
            with self.subTest(x=x, y=y):
                self.assertEqual(result.events, ())
                self.assertIsNone(result.winner)
        valid = parser.parse((line("Delphox used Protect!", .15, .72),), timestamp_ms=1000, source_frame=2)
        self.assertEqual([(e.kind, e.species, e.move) for e in valid.events], [("move", "Delphox", "Protect")])

    def test_screen_selection_uses_located_cues_and_preserves_preview_and_result(self):
        preview = (line("Seleet 4 Pokémon", .38, .17), line("to send into battle.", .38, .215),
                   line("Roku", .21, .055), line("Tomoe", .145, .71), line("3/4", .17, .83))
        self.assertEqual(text_screen(preview), "preview")
        self.assertEqual(useful_lines(preview), preview)
        self.assertEqual(text_screen([line("Active Statuses & Effects", .5, .22)]), "status")
        self.assertEqual(text_screen([line("Active Statuses & Effects", .1, .02)]), "battle")
        self.assertEqual(text_screen([line("Active Statuses & Effects", .5, .22, .6)]), "battle")
        result = (line("WIN", .25, .20),)
        self.assertEqual(text_screen(result), "result")
        self.assertEqual(useful_lines(result), result)

    def test_status_screen_body_cannot_change_active_actors_or_hp(self):
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Delphox",)),
            catalog=ChampionsCatalog(species=("Delphox",)),
        )
        parser._active["p1a"] = "Delphox"
        parser._health["p1a"] = "152/152"
        parser._battle_open = True
        result = parser.parse((line("Active Statuses & Effects", .5, .22),
                               line("Delphox", .08, .86), line("0/152", .14, .93),
                               line("Delphox fainted!", .15, .72)), timestamp_ms=500, source_frame=1)
        self.assertEqual(result.events, ())
        self.assertEqual(parser._active, {"p1a": "Delphox"})
        self.assertEqual(parser._health, {"p1a": "152/152"})

    @unittest.skipUnless(HAS_NUMPY, "requiere NumPy del detector OCR opcional")
    def test_normal_ocr_and_secondary_readers_receive_the_masked_copy(self):
        import numpy as np
        image = np.full((500, 900, 3), 255, dtype=np.uint8)
        engine = RapidOcrEngine.__new__(RapidOcrEngine)
        engine.ocr_screen = "battle"
        samples = []
        def reader(masked):
            samples.append(masked)
            return (line("Rotom", .83, .05), line("background", .45, .04))
        def same_mask(masked, lines):
            self.assertIs(masked, samples[-1])
            self.assertEqual(self.pixel(masked, .45, .04), 0)
            return lines
        engine._read_decoded = reader
        engine._with_second_opinion = same_mask
        engine._rescan_thin_digits = same_mask
        lines, screen = engine._read_text_areas(image)
        self.assertEqual(screen, "battle")
        self.assertEqual([x.text for x in lines], ["Rotom"])
        self.assertEqual(len(samples), 1)
        self.assertTrue((image == 255).all())

    @unittest.skipUnless(HAS_NUMPY, "requiere NumPy del detector OCR opcional")
    def test_screen_transition_reads_the_new_areas_in_the_same_sample(self):
        import numpy as np
        engine = RapidOcrEngine.__new__(RapidOcrEngine)
        engine.ocr_screen = "battle"
        samples = []
        cues = (line("Select 4 Pokémon", .38, .17), line("to send into battle.", .38, .215))
        def reader(masked):
            samples.append(masked)
            extra = (line("Dee Dee", .08, .14),) if self.pixel(masked, .08, .14) else ()
            return cues + extra + (line("3/4", .17, .83),)
        engine._read_decoded = reader
        engine._with_second_opinion = lambda masked, lines: lines
        engine._rescan_thin_digits = lambda masked, lines: lines
        image = np.full((500, 900, 3), 255, dtype=np.uint8)
        lines, engine.ocr_screen = engine._read_text_areas(image)
        self.assertEqual(engine.ocr_screen, "preview")
        self.assertEqual(len(samples), 2)
        self.assertIn("Dee Dee", [x.text for x in lines])
        self.assertIn("3/4", [x.text for x in lines])
        engine._read_text_areas(image)
        self.assertEqual(len(samples), 3)

    @unittest.skipUnless(HAS_NUMPY, "requiere NumPy del detector OCR opcional")
    def test_rotation_happens_before_the_spatial_ocr_mask(self):
        import numpy as np
        engine = RapidOcrEngine.__new__(RapidOcrEngine)
        engine._np = np
        engine._orientation_locked = True
        engine.rotation_quarter_turns = 1
        engine.ocr_screen = "battle"
        image = np.full((900, 500, 3), 255, dtype=np.uint8)
        engine._cv2 = SimpleNamespace(IMREAD_COLOR=1, imdecode=lambda encoded, flag: image)
        def reader(masked):
            self.assertEqual(masked.shape, (500, 900, 3))
            self.assertEqual(self.pixel(masked, .45, .04), 0)
            self.assertEqual(self.pixel(masked, .62, .05), 255)
            return (line("Rotom", .83, .05),)
        engine._read_decoded = reader
        engine._with_second_opinion = lambda masked, lines: lines
        engine._rescan_thin_digits = lambda masked, lines: lines
        self.assertEqual([x.text for x in engine.read(b"encoded frame")], ["Rotom"])
        self.assertTrue((image == 255).all())

    def test_external_engine_preparation_also_filters_lines_and_keeps_the_frame(self):
        engine = SimpleNamespace(read=lambda image: (line("noise", .45, .04), line("Rotom", .83, .05)))
        frame = FramePacket(0, 500, b"original video frame")
        prepared = ChampionsOcrDetector._read_frame(engine, frame)
        self.assertIs(prepared.frame, frame)
        self.assertEqual(prepared.ocr_screen, "battle")
        self.assertEqual([x.text for x in prepared.lines], ["Rotom"])

    def test_percentage_rescans_cover_both_huds_and_exclude_other_text_areas(self):
        engine = RapidOcrEngine.__new__(RapidOcrEngine)
        rescanned = []
        def rescan(image, source, **options):
            rescanned.append(source)
            replacement = line("47%", source.left, source.top)
            self.assertTrue(options["accept"](replacement))
            return replacement
        engine._rescan_zone = rescan
        readings = (line("%", .72, .12), line("%", .9, .28),
                    line("%", .4, .34), line("%", .15, .72))
        corrected = engine._rescan_thin_digits(object(), readings)
        self.assertEqual(rescanned, list(readings[:2]))
        self.assertEqual([r.text for r in corrected], ["47%", "47%", "%", "%"])


if __name__ == "__main__":
    unittest.main()
