import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from services.recognition.face_models import (
    ARCFACE_POINTS,
    ArcFace,
    RetinaFace,
    alignment_matrix,
    fast_nms,
    pose_exemplars,
    reference_embedding,
)

ROOT = Path(__file__).resolve().parents[1]


class InferenceTests(unittest.TestCase):
    def test_alignment_recovers_rotation_scale_and_translation(self):
        angle = .3
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        source = ARCFACE_POINTS @ rotation.T * 2 + [70, 40]
        matrix = alignment_matrix(source)
        mapped = source @ matrix[:, :2].T + matrix[:, 2]
        np.testing.assert_allclose(mapped, ARCFACE_POINTS, atol=1e-4)
        with self.assertRaises(RuntimeError):
            alignment_matrix(np.zeros((5, 2)))

    def test_retinaface_decodes_boxes_landmarks_and_removes_duplicate(self):
        fake = Mock()
        fake.get_inputs.return_value = [Mock(name='input')]
        with patch('services.recognition.face_models.session', return_value=fake):
            detector = RetinaFace('unused.onnx')
        count = len(detector.priors)
        loc = np.zeros((1, count, 4), np.float32)
        conf = np.zeros((1, count, 2), np.float32)
        landmarks = np.zeros((1, count, 10), np.float32)
        # Two different-size anchors decode to the same box and are suppressed.
        conf[0, :2, 1] = [.99, .95]
        loc[0, 1, 2:] = np.log(.5) / .2
        fake.run.return_value = [loc, conf, landmarks]
        _, faces = detector.detect(np.zeros((360, 1280, 3), np.uint8))
        self.assertEqual(faces.shape, (1, 15))
        np.testing.assert_allclose(faces[0, :4], [-8, -8, 32, 32], atol=1e-4)
        np.testing.assert_allclose(faces[0, 4:14].reshape(5, 2), np.full((5, 2), 8))
        blob = fake.run.call_args.args[1][detector.input_name]
        self.assertEqual(blob.shape, (1, 3, 640, 640))
        np.testing.assert_allclose(blob[0, :, -1, -1], 0)
        conf[:] = 0
        self.assertIsNone(detector.detect(np.zeros((360, 1280, 3), np.uint8))[1])

    def test_arcface_rgb_preprocessing_and_invalid_output(self):
        fake = Mock()
        fake.get_inputs.return_value = [Mock(name='input')]
        fake.run.return_value = [np.ones((1, 512), np.float32)]
        with patch('services.recognition.face_models.session', return_value=fake):
            recognizer = ArcFace('unused.onnx')
        image = np.full((112, 112, 3), [0, 127, 255], np.uint8)
        vector = recognizer.feature(image)
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1., places=6)
        blob = fake.run.call_args.args[1][recognizer.input_name]
        np.testing.assert_allclose(blob[0, :, 0, 0], [1, -.5 / 127.5, -1], atol=1e-6)
        fake.run.return_value = [np.zeros((1, 512), np.float32)]
        with self.assertRaises(RuntimeError):
            recognizer.feature(image)

    def test_fast_nms_batched_features_and_pose_exemplars(self):
        boxes = np.array([[10, 10, 50, 50], [12, 12, 50, 50], [200, 200, 40, 40]], dtype=np.float32)
        scores = np.array([0.9, 0.8, 0.85], dtype=np.float32)
        keep = fast_nms(boxes, scores, 0.4)
        np.testing.assert_array_equal(keep, [0, 2])

        fake = Mock()
        fake.get_inputs.return_value = [Mock(name='input')]
        fake.run.return_value = [np.ones((2, 512), np.float32)]
        with patch('services.recognition.face_models.session', return_value=fake):
            recognizer = ArcFace('unused.onnx')
        crop = np.full((112, 112, 3), 120, dtype=np.uint8)
        batched = recognizer.features([crop, crop])
        self.assertEqual(batched.shape, (2, 512))
        np.testing.assert_allclose(np.linalg.norm(batched, axis=1), [1.0, 1.0], atol=1e-6)

        # 40 samples across 5 poses produce 5 distinct normalized pose exemplars
        samples = np.zeros((40, 512), dtype=np.float32)
        for p_idx in range(5):
            samples[p_idx * 8 : (p_idx + 1) * 8, p_idx] = 1.0
        exemplars = pose_exemplars(samples)
        self.assertEqual(exemplars.shape, (5, 512))
        np.testing.assert_allclose(np.linalg.norm(exemplars, axis=1), np.ones(5), atol=1e-6)


if __name__ == '__main__':
    unittest.main()
