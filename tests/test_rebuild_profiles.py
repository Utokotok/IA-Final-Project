import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from services.recognition.face_models import MODEL_ID, PROFILE_NAME, reference_embedding
from scripts.rebuild_profiles import rebuild_folder


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.owner = np.eye(512, dtype=np.float32)[0]
        self.face = np.array([100, 100, 180, 200, 150, 160, 230, 160, 190, 190, 165, 220, 215, 220, .99], np.float32)
        self.image = np.random.default_rng(0).integers(75, 230, (480, 640, 3), dtype=np.uint8)
        self.engine = Mock()
        self.detector = self.set_detector(1)

    def tearDown(self):
        self.temp.cleanup()

    def set_detector(self, count=1):
        detector = Mock()
        detector.detect.return_value = (None, np.stack([self.face] * count))
        self.engine.detector = detector
        return detector

    def enroll(self, name='Alex'):
        folder = self.path / 'person001'
        folder.mkdir(parents=True, exist_ok=True)
        _, jpeg = cv2.imencode('.jpg', self.image)
        images = []
        for stage in ["front", "left", "right", "up", "down"]:
            for num in range(1, 9):
                filename = f"{stage}{num:02d}.jpg"
                (folder / filename).write_bytes(jpeg.tobytes())
                images.append(filename)
        (folder / 'metadata.json').write_text(json.dumps({'name': name, 'images': images}))
        features = np.stack([self.owner] * 40)
        np.savez_compressed(
            folder / PROFILE_NAME,
            name=name,
            features=features,
            model_id=MODEL_ID,
            reference_embedding=reference_embedding(features),
        )
        return folder

    def test_migration_preserves_old_profile_and_is_repeatable(self):
        folder = self.enroll()
        (folder / PROFILE_NAME).unlink()
        old = folder / 'profile.npz'
        old.write_bytes(b'original-sface-archive')
        self.set_detector()
        with patch.object(self.engine, 'feature', return_value=self.owner):
            self.assertEqual(rebuild_folder(self.engine, folder), 'converted 40 photos')
        self.assertEqual(old.read_bytes(), b'original-sface-archive')
        with np.load(folder / PROFILE_NAME, allow_pickle=False) as profile:
            self.assertEqual(profile['model_id'].item(), MODEL_ID)
            self.assertEqual(profile['features'].shape, (40, 512))
        with patch.object(self.engine, 'feature') as feature:
            self.assertEqual(rebuild_folder(self.engine, folder), 'already converted')
            feature.assert_not_called()

    def test_ambiguous_photo_does_not_create_partial_archive(self):
        folder = self.enroll()
        (folder / PROFILE_NAME).unlink()
        self.set_detector(2)
        with self.assertRaises(ValueError):
            rebuild_folder(self.engine, folder)
        self.assertFalse((folder / PROFILE_NAME).exists())
        self.assertEqual(list(folder.glob('.arcface-*')), [])

    def test_different_checkpoint_raises_incompatible_error(self):
        folder = self.enroll()
        np.savez(
            folder / PROFILE_NAME,
            name='Wrong model',
            model_id='other-arcface-checkpoint',
            features=np.stack([self.owner] * 40),
        )
        with self.assertRaises(ValueError):
            rebuild_folder(self.engine, folder)


if __name__ == '__main__':
    unittest.main()
