import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from single_instance import SingleInstanceGuard


class SingleInstanceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.key = f"lineread-test-{uuid4().hex}"

    def make_guard(self):
        guard = SingleInstanceGuard(self.key, lock_directory=Path(self.temp_dir.name))
        self.addCleanup(guard.close)
        return guard

    def test_second_guard_notifies_the_primary_instance(self):
        primary = self.make_guard()
        self.assertTrue(primary.start())
        activation_spy = QSignalSpy(primary.activation_requested)

        secondary = self.make_guard()
        self.assertFalse(secondary.start())
        if activation_spy.count() == 0:
            activation_spy.wait(1000)

        self.assertEqual(activation_spy.count(), 1)

    def test_lock_can_be_acquired_after_primary_closes(self):
        primary = self.make_guard()
        self.assertTrue(primary.start())
        primary.close()

        replacement = self.make_guard()
        self.assertTrue(replacement.start())


if __name__ == "__main__":
    unittest.main()
