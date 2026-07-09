import unittest
from bambu_cli import cli, config, slicer
import argparse


class TestCli(unittest.TestCase):
    def test_argv_json_requested(self):
        self.assertTrue(cli._argv_json_requested(["--json", "status"]))
        self.assertFalse(cli._argv_json_requested(["status"]))

    def test_guess_command(self):
        self.assertEqual(cli._guess_command_from_argv(["status"]), "status")
        self.assertEqual(cli._guess_command_from_argv(["--json", "status"]), "status")
        self.assertEqual(cli._guess_command_from_argv(["--network-timeout", "10", "status"]), "status")


class TestConfig(unittest.TestCase):
    def test_expected_fingerprint(self):
        from tests.bambu_test_base import config_ctx

        with config_ctx({}):
            self.assertIsNone(config._expected_fingerprint())


class TestSlicer(unittest.TestCase):
    def test_slicer_module(self):
        self.assertTrue(hasattr(slicer, "cmd_slice"))
