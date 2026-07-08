from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class TestBambuCmdSlice(unittest.TestCase):
    def setUp(self):
        self.access_patcher = patch('os.access', return_value=True)
        self.mock_access = self.access_patcher.start()

    def tearDown(self):
        self.access_patcher.stop()

    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_invalid_filepath(self, mock_logger):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "-invalid"

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_slice(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)

        mock_logger.error.assert_called_with("Invalid filepath: -invalid")

    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_file_not_found(self, mock_logger, mock_exists):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "notfound.stl"
        mock_exists.return_value = False

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_slice(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)

        mock_logger.error.assert_called_with("File not found: notfound.stl")

    @patch('os.path.exists')
    @patch('subprocess.run')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_step_conversion_fail(self, mock_logger, mock_subprocess_run, mock_exists):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.step"

        # os.path.exists initially true for test.step, false for converted test.stl
        def exists_side_effect(path):
            if path == "test.step":
                return True
            return False
        mock_exists.side_effect = exists_side_effect

        # subprocess.run returns failure
        mock_run_result = MagicMock()
        mock_run_result.returncode = 1
        mock_subprocess_run.return_value = mock_run_result

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_slice(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)

        self.assertTrue(any("STEP conversion failed" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch('subprocess.Popen')
    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    # The baseline test context already pins profiles_dir=/tmp/mock_profiles and
    # orca_slicer=/tmp/mock_orca (conftest resets it per test), so the
    # exists_side_effect branches below are stable across platforms.
    def test_cmd_slice_missing_machine_profile(self, mock_logger, mock_exists, mock_popen):
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.stl"
        args.quality = "standard"
        args.output = "."
        args.copies = 1

        mock_process = MagicMock()
        mock_process.communicate.return_value = ("", "")
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Mock os.path.exists to be True for the STL file, the slicer, and the process file,
        # but False for the machine profile
        def exists_side_effect(path):
            if path == "test.stl":
                return True
            if "OrcaSlicer" in path:
                return True
            if "process" in path and path.endswith(".json"):
                return True # Assuming standard profile exists
            return False # Machine profile or others fail

        mock_exists.side_effect = exists_side_effect

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_slice(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)

        # We need to find what missing profile message is logged
        self.assertTrue(any("Fallback machine profile" in call[0][0] or "not found. Using standard P1P" in call[0][0] for call in mock_logger.warning.call_args_list))

    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.listdir')
    @patch('os.path.getsize')
    @patch('os.unlink')
    @patch('subprocess.Popen')
    @patch('tempfile.NamedTemporaryFile')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('json.load')
    @patch('json.dump')
    @patch('bambu_cli.slicer.logger')
    @patch('bambu_cli.slicer.platform.system', new=lambda: _HOST_SYSTEM)
    def test_cmd_slice_success(self, *mocks):
        mock_logger, mock_json_dump, mock_json_load, mock_open = mocks[:4]
        mock_tempfile, mock_subprocess_run, mock_unlink, mock_getsize = mocks[4:8]
        mock_listdir, mock_isdir, mock_exists = mocks[8:11]
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.stl"
        args.quality = "standard"
        args.output = "."
        args.copies = 1
        args.infill = 15
        args.pattern = "3dhoneycomb"
        args.supports = False
        args.nozzle_temp = 220
        args.bed_temp = 60

        # Let's just make all exists return True
        mock_exists.return_value = True

        # Mock json load
        mock_json_load.return_value = {}

        # Mock tempfile
        mock_temp = MagicMock()
        mock_temp.name = "/tmp/mock_temp.json"
        mock_tempfile.return_value = mock_temp

        # Mock subprocess Popen
        mock_process = _setup_slice_proc(MagicMock())
        mock_subprocess_run.return_value = mock_process

        # Mock getsize
        mock_getsize.return_value = 10240 # 10KB

        cmd_slice(args)

        # Check success message
        self.assertTrue(any("✅ Sliced: ./test_sliced.3mf" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.listdir')
    @patch('os.path.getsize')
    @patch('os.unlink')
    @patch('subprocess.Popen')
    @patch('tempfile.NamedTemporaryFile')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('json.load')
    @patch('json.dump')
    @patch('bambu_cli.slicer.logger')
    @patch('bambu_cli.slicer.platform.system', new=lambda: _HOST_SYSTEM)
    def test_cmd_slice_advanced_settings(self, *mocks):
        mock_logger, mock_json_dump, mock_json_load, mock_open = mocks[:4]
        mock_tempfile, mock_subprocess_run, mock_unlink, mock_getsize = mocks[4:8]
        mock_listdir, mock_isdir, mock_exists = mocks[8:11]
        from bambu_cli.bambu import cmd_slice
        args = MagicMock()
        args.file = "test.stl"
        args.quality = "standard"
        args.output = "."
        args.copies = 1
        args.infill = 15
        args.pattern = "3dhoneycomb"
        args.supports = True
        args.support_type = "tree"
        args.support_interface_density = 50.0
        args.support_interface_pattern = "rectilinear"
        args.walls = 4
        args.wall_type = "archaic"
        args.top_layers = 5
        args.bottom_layers = 4
        args.accel_wall = 500
        args.accel_wall_outer = 300
        args.accel_infill = 800
        args.accel_travel = 1000
        args.accel_first_layer = 200
        args.nozzle_temp = 220
        args.bed_temp = 60

        mock_exists.return_value = True
        mock_json_load.return_value = {}

        mock_temp = MagicMock()
        mock_temp.name = "/tmp/mock_temp.json"
        mock_tempfile.return_value = mock_temp

        # Mock subprocess Popen
        mock_process = _setup_slice_proc(MagicMock())
        mock_subprocess_run.return_value = mock_process
        mock_getsize.return_value = 10240

        cmd_slice(args)

        # Verify advanced settings were passed to json.dump
        process_data = mock_json_dump.call_args_list[0][0][0]
        self.assertEqual(process_data['support_style'], 'tree')
        self.assertEqual(process_data['support_interface_density'], '50.0%')
        self.assertEqual(process_data['support_interface_pattern'], 'rectilinear')
        self.assertEqual(process_data['wall_loops'], '4')
        self.assertEqual(process_data['wall_generator'], 'classic')
        self.assertEqual(process_data['top_shell_layers'], '5')
        self.assertEqual(process_data['bottom_shell_layers'], '4')
        self.assertEqual(process_data['inner_wall_acceleration'], '500')
        self.assertEqual(process_data['outer_wall_acceleration'], '300')
        self.assertEqual(process_data['sparse_infill_acceleration'], '800')
        self.assertEqual(process_data['travel_acceleration'], '1000')
        self.assertEqual(process_data['initial_layer_acceleration'], '200')

        # Check success message with advanced settings
        self.assertTrue(any("✅ Sliced: ./test_sliced.3mf" in call[0][0] for call in mock_logger.info.call_args_list))


class TestConvertStepToStl(unittest.TestCase):

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('os.path.exists')
    @patch('os.path.getsize')
    @patch('bambu_cli.bambu.logger')
    def test_convert_step_to_stl_success(self, mock_logger, mock_getsize, mock_exists, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_exists.return_value = True
        mock_getsize.return_value = 2048

        mock_conv = MagicMock()
        mock_conv.returncode = 0
        mock_run.return_value = mock_conv

        import os
        abs_step = os.path.abspath("test.step")
        abs_stl = os.path.abspath("test.stl")
        stl_path, success = _convert_step_to_stl("test.step")

        self.assertTrue(success)
        self.assertTrue(stl_path.endswith("test_.stl"))
        self.assertIn("bambu_step_", stl_path)

        self.assertEqual(mock_run.call_count, 1)
        cmd_run = mock_run.call_args[0][0]
        self.assertIn("gmsh", cmd_run)
        self.assertIn(abs_step, cmd_run)
        self.assertIn("-o", cmd_run)
        out_idx = cmd_run.index("-o") + 1
        self.assertEqual(cmd_run[out_idx], stl_path)
        mock_logger.info.assert_called_with(f"   Converted: {os.path.basename(stl_path)} (2KB)")

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('bambu_cli.bambu.logger')
    def test_convert_step_to_stl_failure_return_code(self, mock_logger, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_conv = MagicMock()
        mock_conv.returncode = 1
        mock_run.return_value = mock_conv

        stl_path, success = _convert_step_to_stl("test.step")

        self.assertFalse(success)
        self.assertIsNone(stl_path)
        mock_logger.error.assert_called_with("STEP conversion failed.")

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('os.path.exists')
    @patch('bambu_cli.bambu.logger')
    def test_convert_step_to_stl_failure_no_file(self, mock_logger, mock_exists, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_exists.return_value = False

        mock_conv = MagicMock()
        mock_conv.returncode = 0
        mock_run.return_value = mock_conv

        stl_path, success = _convert_step_to_stl("test.step")

        self.assertFalse(success)
        self.assertIsNone(stl_path)
        mock_logger.error.assert_called_with("STEP conversion failed.")

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('bambu_cli.bambu.logger')
    def test_convert_step_to_stl_filenotfounderror(self, mock_logger, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_run.side_effect = FileNotFoundError()

        stl_path, success = _convert_step_to_stl("test.step")

        self.assertFalse(success)
        self.assertIsNone(stl_path)
        mock_logger.error.assert_called_with("STEP conversion failed. Please install gmsh for your platform.")


class TestBambuCmdSliceEdgeCases(unittest.TestCase):
    def setUp(self):
        self.access_patcher = patch('os.access', return_value=True)
        self.mock_access = self.access_patcher.start()

    def tearDown(self):
        self.access_patcher.stop()

    @patch('bambu_cli.bambu._convert_step_to_stl')
    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_step_conversion_error(self, mock_logger, mock_exists, mock_convert):
        from bambu_cli.bambu import cmd_slice
        mock_exists.return_value = True
        mock_convert.return_value = (None, False)

        args = MagicMock()
        args.file = "test.step"

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_slice(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)

        mock_convert.assert_called_once_with("test.step")

    @patch('bambu_cli.bambu.subprocess.run')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_convert_step_to_stl_file_not_found(self, mock_logger, mock_run):
        from bambu_cli.bambu import _convert_step_to_stl
        mock_run.side_effect = FileNotFoundError("gmsh not found")

        filepath, success = _convert_step_to_stl("test.step")

        self.assertFalse(success)
        mock_logger.error.assert_called_with("STEP conversion failed. Please install gmsh for your platform.")

    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_invalid_output_dir(self, mock_logger, mock_exists):
        from bambu_cli.bambu import cmd_slice
        mock_exists.return_value = True

        args = MagicMock()
        args.file = "test.stl"
        args.output = "-invalid_dir"

        with self.assertRaises((SystemExit, BambuError)) as cm:
            cmd_slice(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)

        mock_logger.error.assert_called_with("Invalid output directory: -invalid_dir")

    @patch('subprocess.Popen')
    @patch('bambu_cli.slicer._create_temp_profiles')
    @patch('os.unlink')
    @patch('os.path.getsize', return_value=1024)
    @patch('os.path.exists')
    # With os.path.exists mocked True, real makedirs('/tmp/out') skips parent
    # creation and fails on Windows (WinError 3) unless the dir already exists.
    @patch('os.makedirs', MagicMock())
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_copies_logic(self, mock_logger, mock_exists, mock_getsize, mock_unlink, mock_create, mock_run):
        from bambu_cli.bambu import cmd_slice
        mock_exists.return_value = True

        mock_process = MagicMock()
        mock_process.name = "process.json"
        mock_filament = MagicMock()
        mock_filament.name = "filament.json"
        mock_create.return_value = (mock_process, mock_filament)

        # Mock Popen
        mock_proc = _setup_slice_proc(MagicMock())
        mock_run.return_value = mock_proc

        args = MagicMock()
        args.file = "test.stl"
        args.output = "/tmp/out"
        args.copies = 2
        args.quality = "standard"
        args.supports = False
        args.nozzle_temp = 220
        args.bed_temp = 60
        args.infill = 15
        args.pattern = "grid"

        with settings_ctx(profiles_dir='/tmp', orca_slicer='/tmp/orca'), \
                patch('platform.system', return_value="Windows"):
            cmd_slice(args)

        call_args = mock_run.call_args[0][0]
        self.assertIn("--arrange", call_args)
        self.assertIn("1", call_args)

        outfile_idx = call_args.index("--export-3mf") + 1
        self.assertEqual(call_args[outfile_idx], "test_x2_sliced.3mf")

    @patch('bambu_cli.config.detect_profiles_dir', return_value='/real/OrcaSlicer/profiles/BBL')
    @patch('os.listdir', return_value=['Bambu PLA Basic @base.json'])
    @patch('os.path.isdir', return_value=True)
    @patch('os.path.exists')
    @patch('os.makedirs', MagicMock())
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_missing_profile_reports_detected_dir(
        self, mock_logger, mock_exists, mock_isdir, mock_listdir, mock_detect
    ):
        """A missing machine profile surfaces the configured + detected profiles dir."""
        from bambu_cli.bambu import cmd_slice
        import bambu_cli.utils as utils

        sep = os.sep
        # Everything resolves except the machine profile directory contents.
        mock_exists.side_effect = lambda path: f"{sep}machine{sep}" not in path

        args = MagicMock()
        args.file = "test.stl"
        args.output = "/tmp/out"
        args.copies = 1
        args.quality = "standard"
        args.filament = "PLA Basic"

        utils._LAST_ERROR_PAYLOAD = None
        with settings_ctx(profiles_dir='/tmp/mock_profiles', orca_slicer='/tmp/orca'):
            with self.assertRaises((SystemExit, BambuError)) as cm:
                cmd_slice(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 1)  # EXIT_CONFIG_ERROR

        payload = utils._LAST_ERROR_PAYLOAD
        self.assertEqual(payload["failed_step"], "profiles")
        self.assertEqual(payload["profile"], "machine")
        self.assertEqual(payload["profiles_dir"], "/tmp/mock_profiles")
        self.assertEqual(payload["detected_profiles_dir"], "/real/OrcaSlicer/profiles/BBL")

    @patch('os.path.exists')
    @patch('os.makedirs', MagicMock())
    @patch('bambu_cli.bambu.logger')
    @patch('sys.exit')
    def test_cmd_slice_orca_slicer_not_found(self, mock_exit, mock_logger, mock_exists):
        from bambu_cli.bambu import cmd_slice
        def fake_exists(path):
            if "orca" in path.lower():
                return False
            return True
        mock_exists.side_effect = fake_exists

        args = MagicMock()
        args.file = "test.stl"
        args.output = "/tmp/out"
        args.copies = 1

        mock_exit.side_effect = SystemExit(1)

        with settings_ctx(orca_slicer='/tmp/missing_orca'):
            with self.assertRaises((SystemExit, BambuError)):
                cmd_slice(args)

        mock_logger.error.assert_called_with("OrcaSlicer not found at /tmp/missing_orca")

    @patch('os.listdir')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_discover_process_profile_branches(self, mock_logger, mock_exists, mock_isdir, mock_listdir):
        from bambu_cli.bambu import _discover_process_profile
        mock_isdir.return_value = True
        quality_map = {"standard": "0.20mm Standard @BBL P1P"}

        with settings_ctx(profiles_dir='/tmp'):
            mock_listdir.return_value = ["0.20mm Standard @BBL P1P.json"]
            result = _discover_process_profile("standard", quality_map)
            self.assertTrue("0.20mm Standard @BBL P1P.json" in result)

            mock_listdir.return_value = ["0.20mm Extra @BBL P1P.json"]
            result = _discover_process_profile("missing", quality_map)
            self.assertTrue("0.20mm Extra @BBL P1P.json" in result)

            # Test fallback to 0.20mm when requested layer height is not found
            mock_listdir.return_value = ["0.20mm Extra @BBL P1P.json"]
            result = _discover_process_profile("0.16mm", quality_map)
            self.assertTrue("0.20mm Extra @BBL P1P.json" in result)
            mock_logger.warning.assert_called_with("⚠️  Requested quality not found, using: 0.20mm Extra @BBL P1P.json")

            # Test no slicer profiles found at all
            mock_listdir.return_value = []
            result = _discover_process_profile("standard", quality_map)
            self.assertIsNone(result)
            mock_logger.error.assert_called_with(f"No slicer profiles found in {os.path.join('/tmp', 'process')}")

            # Test directory does not exist
            mock_isdir.return_value = False
            result = _discover_process_profile("standard", quality_map)
            self.assertIsNone(result)

    @patch('subprocess.Popen')
    @patch('bambu_cli.slicer._create_temp_profiles')
    @patch('os.unlink')
    @patch('os.path.getsize', return_value=1024)
    @patch('os.path.exists')
    @patch('os.makedirs', MagicMock())
    @patch('bambu_cli.slicer.logger')
    def test_cmd_slice_error_message_parsing(self, mock_logger, mock_exists, mock_getsize, mock_unlink, mock_create, mock_run):
        from bambu_cli.bambu import cmd_slice

        def fake_exists(path):
            if path == os.path.join("/tmp/out", "test_sliced.3mf"):
                return False
            return True
        mock_exists.side_effect = fake_exists

        mock_process = MagicMock()
        mock_process.name = "process.json"
        mock_filament = MagicMock()
        mock_filament.name = "filament.json"
        mock_create.return_value = (mock_process, mock_filament)

        # Mock Popen
        mock_proc = _setup_slice_proc(
            MagicMock(),
            returncode=1,
            stdout=b"2024-01-01 ] [error] Missing wall settings\n2024-01-01 nothing to be sliced",
        )
        mock_run.return_value = mock_proc

        args = MagicMock()
        args.file = "test.stl"
        args.output = "/tmp/out"
        args.copies = 1
        args.quality = "standard"
        args.supports = False
        args.nozzle_temp = 220
        args.bed_temp = 60
        args.infill = 15
        args.pattern = "grid"

        with settings_ctx(profiles_dir='/tmp', orca_slicer='/tmp/orca'), \
                patch('platform.system', return_value="Windows"):
            with self.assertRaises((SystemExit, BambuError)) as cm:
                cmd_slice(args)
            self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)

        mock_logger.error.assert_any_call("   Missing wall settings")
        mock_logger.error.assert_any_call("   2024-01-01 nothing to be sliced")


if __name__ == '__main__':
    unittest.main()
