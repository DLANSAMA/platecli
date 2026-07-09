from tests.bambu_test_base import *  # noqa: F401,F403
from bambu_cli.errors import BambuError


class _FakeResp:
    """Minimal urllib-response stand-in usable as a context manager.

    ``read(n)`` yields the queued chunks then b"", ``getheader`` maps header
    names to values (missing -> None like the real API), and ``geturl`` returns
    the post-redirect URL (or None when there was no redirect).
    """

    def __init__(self, chunks, headers=None, final_url=None):
        self._chunks = list(chunks)
        self._headers = headers or {}
        self._final_url = final_url

    def read(self, n=None):
        return self._chunks.pop(0) if self._chunks else b""

    def getheader(self, name):
        return self._headers.get(name)

    def geturl(self):
        return self._final_url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestResolvePrintablesUrl(unittest.TestCase):
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_printables_model_not_found(self, mock_logger):
        from bambu_cli.printables import _get_printables_file_info
        import json

        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"data": {"print": None}}).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertIsNone(fid)
        self.assertIsNone(ftype)
        self.assertIsNone(fname)
        mock_logger.error.assert_called_with("Model #123 not found on Printables")

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_printables_no_valid_files(self, mock_logger):
        from bambu_cli.printables import _get_printables_file_info
        import json

        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {
                "data": {
                    "print": {
                        "name": "Test",
                        "stls": [{"id": "1", "name": "part1.txt", "fileSize": 1024}],
                        "gcodes": [],
                    }
                }
            }
        ).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertIsNone(fid)
        self.assertIsNone(ftype)
        self.assertIsNone(fname)
        mock_logger.error.assert_called_with("No STL, STEP, or 3MF files found for this model")

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_printables_url_error(self, mock_logger):
        from bambu_cli.printables import _get_printables_file_info
        import urllib.error

        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.URLError("Network unreachable")

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertIsNone(fid)
        self.assertIsNone(ftype)
        self.assertIsNone(fname)
        mock_logger.error.assert_called_with(
            "Network error querying Printables API: <urlopen error Network unreachable>"
        )

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_printables_multiple_stls(self, mock_logger):
        from bambu_cli.printables import _get_printables_file_info
        import json

        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {
                "data": {
                    "print": {
                        "name": "Test",
                        "stls": [
                            {"id": "1", "name": "part1.stl", "fileSize": 1024},
                            {"id": "2", "name": "part2.stl", "fileSize": 2048},
                        ],
                    }
                }
            }
        ).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertEqual(fid, "2")
        self.assertEqual(ftype, "stl")
        mock_logger.info.assert_any_call("   Found 2 STL files:")

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_printables_multiple_steps(self, mock_logger):
        from bambu_cli.printables import _get_printables_file_info
        import json

        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {
                "data": {
                    "print": {
                        "name": "Test",
                        "stls": [
                            {"id": "1", "name": "part1.step", "fileSize": 1024},
                            {"id": "2", "name": "part2.step", "fileSize": 2048},
                        ],
                    }
                }
            }
        ).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertEqual(fid, "2")
        self.assertEqual(ftype, "stl")
        mock_logger.info.assert_any_call("   Found 2 STEP files:")

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_printables_3mf_fallback(self, mock_logger):
        from bambu_cli.printables import _get_printables_file_info
        import json

        mock_opener = MagicMock()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {
                "data": {
                    "print": {
                        "name": "Test",
                        "stls": [{"id": "1", "name": "part1.3mf", "fileSize": 1024}],
                        "gcodes": [{"id": "2", "name": "part2.3mf", "fileSize": 2048}],
                    }
                }
            }
        ).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertEqual(fid, "2")
        # 3MF from gcodes sets type="gcode"
        self.assertEqual(ftype, "gcode")

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_printables_generic_exception(self, mock_logger):
        from bambu_cli.printables import _get_printables_file_info

        mock_opener = MagicMock()
        mock_opener.open.side_effect = Exception("Generic Fetch Error")

        fid, ftype, fname = _get_printables_file_info("123", {}, mock_opener)
        self.assertIsNone(fid)
        mock_logger.error.assert_called_with("Failed to query Printables API: Generic Fetch Error")

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_get_printables_download_link_error(self, mock_logger):
        from bambu_cli.printables import _get_printables_download_link
        import json

        mock_opener = MagicMock()

        mock_resp = MagicMock()
        # Mock API returning None link
        mock_resp.read.return_value = json.dumps({"data": {"fileDownloadLink": None}}).encode()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        result = _get_printables_download_link("1", "1", "stl", "name.stl", {}, mock_opener)
        self.assertEqual(result, (None, None))
        mock_logger.error.assert_called_with("Failed to get download link: unknown error")

        # Test exception path
        mock_opener.open.side_effect = Exception("Link Fetch Error")
        result = _get_printables_download_link("1", "1", "stl", "name.stl", {}, mock_opener)
        self.assertEqual(result, (None, None))
        mock_logger.error.assert_called_with("Failed to get download link: Link Fetch Error")

    @patch("bambu_cli.printables.build_safe_opener")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_resolve_printables_url_success(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.printables import resolve_printables_url
        import json

        # First call: GraphQL query for model details
        mock_response_1 = MagicMock()
        mock_response_1.read.return_value = json.dumps(
            {
                "data": {
                    "print": {
                        "name": "Test Model",
                        "stls": [{"name": "part1.stl", "fileSize": 1024, "id": "file_123"}],
                        "gcodes": [],
                    }
                }
            }
        ).encode()

        # Second call: GraphQL mutation for download link
        mock_response_2 = MagicMock()
        mock_response_2.read.return_value = json.dumps(
            {"data": {"getDownloadLink": {"ok": True, "output": {"link": "https://download.example.com/part1.stl"}}}}
        ).encode()

        # Set side effect for urlopen context manager
        mock_urlopen.return_value.__enter__.side_effect = [mock_response_1, mock_response_2]

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertEqual(download_url, "https://download.example.com/part1.stl")
        self.assertEqual(filename, "part1.stl")

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_resolve_printables_url_not_printables(self, mock_logger):
        from bambu_cli.printables import resolve_printables_url

        url = "https://www.thingiverse.com/thing:12345"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

    @patch("bambu_cli.printables.build_safe_opener")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_resolve_printables_model_not_found(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.printables import resolve_printables_url
        import json

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"data": {"print": None}}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

        self.assertTrue(
            any("Model #12345 not found on Printables" in call[0][0] for call in mock_logger.error.call_args_list)
        )

    @patch("bambu_cli.printables.build_safe_opener")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_resolve_printables_no_valid_files(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.printables import resolve_printables_url
        import json

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"data": {"print": {"name": "Test Model", "stls": [], "gcodes": []}}}
        ).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

        self.assertTrue(
            any(
                "No STL, STEP, or 3MF files found for this model" in call[0][0]
                for call in mock_logger.error.call_args_list
            )
        )

    @patch("bambu_cli.printables.build_safe_opener")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_resolve_printables_prioritize_step(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.printables import resolve_printables_url
        import json

        mock_response_1 = MagicMock()
        mock_response_1.read.return_value = json.dumps(
            {
                "data": {
                    "print": {
                        "name": "Test Model",
                        "stls": [{"name": "part1.step", "fileSize": 1024, "id": "file_123"}],
                        "gcodes": [],
                    }
                }
            }
        ).encode()

        mock_response_2 = MagicMock()
        mock_response_2.read.return_value = json.dumps(
            {"data": {"getDownloadLink": {"ok": True, "output": {"link": "https://download.example.com/part1.step"}}}}
        ).encode()

        mock_urlopen.return_value.__enter__.side_effect = [mock_response_1, mock_response_2]

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertEqual(download_url, "https://download.example.com/part1.step")
        self.assertEqual(filename, "part1.step")

        self.assertTrue(any("→ Using STEP: part1.step (1KB)" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch("bambu_cli.printables.build_safe_opener")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_resolve_printables_prioritize_3mf(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.printables import resolve_printables_url
        import json

        mock_response_1 = MagicMock()
        mock_response_1.read.return_value = json.dumps(
            {
                "data": {
                    "print": {
                        "name": "Test Model",
                        "stls": [],
                        "gcodes": [{"name": "part1.3mf", "fileSize": 1024, "id": "file_123"}],
                    }
                }
            }
        ).encode()

        mock_response_2 = MagicMock()
        mock_response_2.read.return_value = json.dumps(
            {"data": {"getDownloadLink": {"ok": True, "output": {"link": "https://download.example.com/part1.3mf"}}}}
        ).encode()

        mock_urlopen.return_value.__enter__.side_effect = [mock_response_1, mock_response_2]

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertEqual(download_url, "https://download.example.com/part1.3mf")
        self.assertEqual(filename, "part1.3mf")

        self.assertTrue(any("falling back to 3MF" in call[0][0] for call in mock_logger.warning.call_args_list))
        self.assertTrue(any("→ Using 3MF: part1.3mf (1KB)" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch("bambu_cli.printables.build_safe_opener")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_resolve_printables_download_link_error(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.printables import resolve_printables_url
        import json

        mock_response_1 = MagicMock()
        mock_response_1.read.return_value = json.dumps(
            {
                "data": {
                    "print": {
                        "name": "Test Model",
                        "stls": [{"name": "part1.stl", "fileSize": 1024, "id": "file_123"}],
                        "gcodes": [],
                    }
                }
            }
        ).encode()

        mock_response_2 = MagicMock()
        mock_response_2.read.return_value = json.dumps(
            {
                "data": {
                    "getDownloadLink": {
                        "ok": False,
                        "errors": [{"field": "link", "messages": ["Download limit reached"]}],
                    }
                }
            }
        ).encode()

        mock_urlopen.return_value.__enter__.side_effect = [mock_response_1, mock_response_2]

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

        self.assertTrue(
            any(
                "Failed to get download link: Download limit reached" in call[0][0]
                for call in mock_logger.error.call_args_list
            )
        )

    @patch("bambu_cli.printables.build_safe_opener")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_resolve_printables_exception(self, mock_logger, mock_safe_opener):
        mock_urlopen = mock_safe_opener.return_value.open
        from bambu_cli.printables import resolve_printables_url

        mock_urlopen.return_value.__enter__.side_effect = urllib.error.URLError("Network failure")

        url = "https://www.printables.com/model/12345-test-model"
        download_url, filename = resolve_printables_url(url)

        self.assertIsNone(download_url)
        self.assertIsNone(filename)

        self.assertTrue(
            any("Network error querying Printables API" in call[0][0] for call in mock_logger.error.call_args_list)
        )


class TestBambuCmdDownload(unittest.TestCase):
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_invalid_output_dir(self, mock_logger):
        from bambu_cli.commands import cmd_download

        args = MagicMock()
        args.url = "http://example.com/test.stl"
        args.output = "-invalid_dir"

        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)

        mock_logger.error.assert_called_with("Invalid output directory: -invalid_dir")

    @patch("urllib.request.Request")
    @patch("bambu_cli.logging_utils._BACKEND")
    @patch("builtins.open", new_callable=mock_open)
    def test_cmd_download_sanitization_fallback(self, mock_file, mock_logger, mock_req):
        from bambu_cli.commands import cmd_download
        import urllib.request

        args = MagicMock()
        # Create a URL where os.path.basename(unquote(path)) evaluates to something invalid
        # For instance, URL path is just /.. or /... -> basename evaluates to ..
        args.url = "http://example.com/.."
        args.output = "/tmp/out"
        args.name = None

        mock_opener = MagicMock()
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b"data", b""]
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        self._run_download(args, opener=mock_opener)

        # Path should fall back to model.stl, then get appended to output
        mock_file.assert_called_with(os.path.join("/tmp/out", "model.stl"), "wb")
        mock_logger.info.assert_any_call("⬇️  Downloading model.stl...")

    def setUp(self):
        self.mock_safe_opener = MagicMock()
        self.mock_safe_opener.open = MagicMock()
        self.exists_patcher = patch("os.path.exists", return_value=False)
        self.mock_exists = self.exists_patcher.start()
        self.getsize_patcher = patch("os.path.getsize", return_value=1024)
        self.mock_getsize = self.getsize_patcher.start()
        self._resolve = MagicMock(return_value=(None, None))
        self._noncolliding = lambda p: p

    def tearDown(self):
        self.exists_patcher.stop()
        self.getsize_patcher.stop()

    def _run_download(self, args, opener=None, resolve=None, noncolliding=None):
        from bambu_cli.commands import cmd_download

        return cmd_download(
            args,
            opener_factory=lambda: opener if opener is not None else self.mock_safe_opener,
            resolve_printables=resolve if resolve is not None else self._resolve,
            noncolliding_path=noncolliding if noncolliding is not None else self._noncolliding,
        )

    @patch("os.path.getsize")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_with_printables_url(self, mock_logger, mock_open, mock_getsize):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download

        # Mock resolve to return a resolved URL and filename
        self._resolve.return_value = ("https://download.example.com/part1.stl", "part1.stl")

        args = MagicMock()
        args.url = "https://www.printables.com/model/12345"
        args.output = "."
        args.name = None

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_getsize.return_value = 1024

        self._run_download(args)

        self._resolve.assert_called_once_with("https://www.printables.com/model/12345")
        mock_urlopen.assert_called_once()

        # Check success message
        self.assertTrue(any("✅ Downloaded: ./part1.stl" in call[0][0] for call in mock_logger.info.call_args_list))

    @patch("os.path.getsize")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_direct_url_success(self, mock_logger, mock_open, mock_getsize):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download

        args = MagicMock()
        args.url = "https://example.com/model.stl"
        args.output = "."
        args.name = None

        self._resolve.return_value = (None, None)

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_getsize.return_value = 1024

        self._run_download(args)

        self._resolve.assert_called_once_with("https://example.com/model.stl")
        mock_urlopen.assert_called_once()

        # Check success message
        self.assertTrue(
            any("✅ Downloaded: ./model.stl (1KB)" in call[0][0] for call in mock_logger.info.call_args_list)
        )

    @patch("os.path.getsize")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_custom_name(self, mock_logger, mock_open, mock_getsize):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download

        args = MagicMock()
        args.url = "https://example.com/model.stl"
        args.output = "."
        args.name = "custom.stl"

        self._resolve.return_value = (None, None)

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_getsize.return_value = 1024

        self._run_download(args)

        self._resolve.assert_called_once_with("https://example.com/model.stl")
        mock_urlopen.assert_called_once()

        # Check success message
        self.assertTrue(
            any("✅ Downloaded: ./custom.stl (1KB)" in call[0][0] for call in mock_logger.info.call_args_list)
        )

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_printables_fail(self, mock_logger):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download

        args = MagicMock()
        args.url = "https://www.printables.com/model/12345"
        args.output = "."
        args.name = None

        self._resolve.return_value = (None, None)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)

        self._resolve.assert_called_once_with("https://www.printables.com/model/12345")
        mock_urlopen.assert_not_called()

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_http_error(self, mock_logger):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download
        import urllib.error

        args = MagicMock()
        args.url = "https://example.com/model.stl"
        args.output = "."
        args.name = None

        self._resolve.return_value = (None, None)

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://example.com/model.stl", code=404, msg="Not Found", hdrs={}, fp=None
        )

        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)

        self._resolve.assert_called_once_with("https://example.com/model.stl")
        mock_urlopen.assert_called_once()

        self.assertTrue(
            any("Download failed: HTTP Error 404" in call[0][0] for call in mock_logger.error.call_args_list)
        )

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_generic_error(self, mock_logger):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download

        args = MagicMock()
        args.url = "https://example.com/model.stl"
        args.output = "."
        args.name = None

        self._resolve.return_value = (None, None)

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)

        self._resolve.assert_called_once_with("https://example.com/model.stl")
        mock_urlopen.assert_called_once()

        self.assertTrue(
            any(
                "Network error during download: <urlopen error Connection refused>" in call[0][0]
                for call in mock_logger.error.call_args_list
            )
        )

    @patch("os.path.getsize")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_missing_extension(self, mock_logger, mock_open, mock_getsize):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download

        args = MagicMock()
        args.url = "https://example.com/model"
        args.output = "."
        args.name = None

        self._resolve.return_value = (None, None)

        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_getsize.return_value = 1024

        self._run_download(args)

        self._resolve.assert_called_once_with("https://example.com/model")
        mock_urlopen.assert_called_once()

        # Check success message with .stl appended
        self.assertTrue(
            any("✅ Downloaded: ./model.stl (1KB)" in call[0][0] for call in mock_logger.info.call_args_list)
        )

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_invalid_scheme(self, mock_logger):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download

        args = MagicMock()
        args.url = "file:///etc/passwd"
        args.output = "."
        args.name = None

        self._resolve.return_value = (None, None)

        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(args)
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)

        # urllib.request.urlopen should NOT be called
        mock_urlopen.assert_not_called()

        # Check for invalid scheme error message
        self.assertTrue(any("Invalid URL scheme: file" in call[0][0] for call in mock_logger.error.call_args_list))

    @patch("os.path.getsize")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_cmd_download_path_traversal_sanitization(self, mock_logger, mock_open, mock_getsize):
        mock_urlopen = self.mock_safe_opener.open
        from bambu_cli.commands import cmd_download

        # A URL containing an encoded path traversal attempt
        args = MagicMock()
        args.url = "https://example.com/models/file.stl%2f..%2f..%2fetc%2fpasswd"
        args.output = "/tmp"
        args.name = None

        self._resolve.return_value = (None, None)
        mock_response = MagicMock()
        mock_response.read.side_effect = [b"test data", b""]
        self.mock_safe_opener.open.return_value.__enter__.return_value = mock_response
        mock_urlopen.return_value.__enter__.return_value = mock_response
        mock_getsize.return_value = 1024

        self._run_download(args)

        expected_filename = "passwd.stl"
        expected_path = os.path.join("/tmp", expected_filename)

        # open() is called with the sanitized native path (native separators).
        mock_open.assert_called_once_with(expected_path, "wb")

        # The success log normalizes separators to '/' via _path_for_message, so
        # compare against a separately-normalized display string. This keeps the
        # native-path mock_open check above intact while matching the log on Windows.
        expected_display = expected_path.replace(os.sep, "/")
        self.assertTrue(
            any(f"✅ Downloaded: {expected_display}" in call[0][0] for call in mock_logger.info.call_args_list)
        )


class TestDownloadLoopBranches(unittest.TestCase):
    """Error/limit branches of the _cmd_download HTTP loop, driven with a fake
    response so no network or real filesystem writes occur."""

    def setUp(self):
        self.mock_safe_opener = MagicMock()
        self.mock_open = self.mock_safe_opener.open
        self._resolve = MagicMock(return_value=(None, None))
        self.exists_patcher = patch("os.path.exists", return_value=False)
        self.exists_patcher.start()
        self.getsize_patcher = patch("os.path.getsize", return_value=1024)
        self.mock_getsize = self.getsize_patcher.start()
        self._noncolliding = lambda p: p
        self.file_patcher = patch("builtins.open", new_callable=mock_open)
        self.file_patcher.start()

    def tearDown(self):
        for p in (self.file_patcher, self.getsize_patcher, self.exists_patcher):
            p.stop()

    def _run_download(self, args, opener=None, resolve=None, noncolliding=None):
        from bambu_cli.commands import cmd_download

        return cmd_download(
            args,
            opener_factory=lambda: opener if opener is not None else self.mock_safe_opener,
            resolve_printables=resolve if resolve is not None else self._resolve,
            noncolliding_path=noncolliding if noncolliding is not None else self._noncolliding,
        )

    def _args(self, url="https://example.com/model.stl", **over):
        args = MagicMock()
        args.url = url
        args.output = "."
        args.name = None
        for k, v in over.items():
            setattr(args, k, v)
        return args

    def _respond(self, resp):
        self.mock_open.return_value = resp

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_content_length_over_limit_rejected(self, mock_logger):
        from bambu_cli.commands import cmd_download

        self._respond(_FakeResp([b"", b""], headers={"Content-Length": str(5 * 1024 * 1024)}))
        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(self._args(max_download_mb=1))
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        self.assertTrue(any("too large" in c[0][0] for c in mock_logger.error.call_args_list))

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_streaming_over_limit_rejected(self, mock_logger):
        from bambu_cli.commands import cmd_download

        # No Content-Length; a single chunk larger than the 1 MB cap trips the
        # in-loop guard.
        self._respond(_FakeResp([b"x" * (2 * 1024 * 1024), b""]))
        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(self._args(max_download_mb=1))
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        self.assertTrue(any("exceeded the" in c[0][0] for c in mock_logger.error.call_args_list))

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_empty_download_rejected(self, mock_logger):
        from bambu_cli.commands import cmd_download

        self.mock_getsize.return_value = 0
        self._respond(_FakeResp([b"data", b""]))
        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(self._args())
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        self.assertTrue(any("empty" in c[0][0] for c in mock_logger.error.call_args_list))

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_truncated_download_rejected(self, mock_logger):
        from bambu_cli.commands import cmd_download

        self._respond(_FakeResp([b"x" * 10, b""], headers={"Content-Length": "1000"}))
        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(self._args())
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        self.assertTrue(any("ended early" in c[0][0] for c in mock_logger.error.call_args_list))

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_ssrf_urlerror_reported_as_security_violation(self, mock_logger):
        import urllib.error
        from bambu_cli.commands import cmd_download

        self.mock_open.side_effect = urllib.error.URLError("Security Error: blocked host")
        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(self._args())
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 5)
        self.assertTrue(any("SSRF Security Violation" in c[0][0] for c in mock_logger.error.call_args_list))

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_oserror_reported_as_local_file_error(self, mock_logger):
        from bambu_cli.commands import cmd_download

        self.mock_open.side_effect = OSError("No space left on device")
        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(self._args())
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        self.assertTrue(any("Local file error" in c[0][0] for c in mock_logger.error.call_args_list))

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_generic_exception_reported(self, mock_logger):
        from bambu_cli.commands import cmd_download

        self.mock_open.side_effect = RuntimeError("kaboom")
        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(self._args())
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 2)
        self.assertTrue(any("Download failed: kaboom" in c[0][0] for c in mock_logger.error.call_args_list))

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_html_page_without_model_link_fails(self, mock_logger):
        from bambu_cli.commands import cmd_download

        self._respond(
            _FakeResp(
                [b"<html><body>no model links here</body></html>", b""],
                headers={"Content-Type": "text/html"},
            )
        )
        with self.assertRaises((SystemExit, BambuError)) as cm:
            self._run_download(self._args(url="https://example.com/page"))
        self.assertEqual(getattr(cm.exception, "exit_code", getattr(cm.exception, "code", None)), 3)
        self.assertTrue(
            any("did not contain a direct model file link" in c[0][0] for c in mock_logger.error.call_args_list)
        )

    @patch("bambu_cli.logging_utils._BACKEND")
    def test_redirect_url_is_revalidated_and_used(self, mock_logger):
        from bambu_cli.commands import cmd_download

        # geturl() reports a post-redirect URL; the loop must re-validate it and
        # recompute the output filename from the final URL.
        self._respond(
            _FakeResp(
                [b"stl bytes", b""],
                final_url="https://example.com/final.stl",
            )
        )
        self._run_download(self._args(url="https://example.com/start.stl"))
        self.assertTrue(any("final.stl" in c[0][0] for c in mock_logger.info.call_args_list))

    @patch("bambu_cli.download.downloader._resolve_html_model_link")
    @patch("bambu_cli.logging_utils._BACKEND")
    def test_html_page_resolving_to_model_link_downloads_it(self, mock_logger, mock_resolve_html):
        mock_resolve_html.return_value = ("https://example.com/found.stl", "found.stl")
        html = _FakeResp([b"<html>link</html>", b""], headers={"Content-Type": "text/html"})
        stl = _FakeResp([b"stl bytes", b""], headers={"Content-Type": "application/octet-stream"})
        self.mock_open.side_effect = [html, stl]
        self._run_download(self._args(url="https://example.com/page"))
        # Second loop iteration fetches the resolved direct link.
        self.assertTrue(any("Found model file link" in c[0][0] for c in mock_logger.info.call_args_list))
        self.assertTrue(any("found.stl" in c[0][0] for c in mock_logger.info.call_args_list))


if __name__ == "__main__":
    unittest.main()
