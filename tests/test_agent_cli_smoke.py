import unittest
import tempfile
import pathlib

from tests.agent_cli_smoke import (
    smoke_help_surface,
    smoke_setup_json,
    smoke_setup_json_noninteractive_missing_values,
    smoke_setup_json_rejects_bad_access_code_file,
    smoke_invalid_config_json,
    smoke_parse_error_json,
    smoke_preflight_json,
    smoke_local_job_dry_run_json,
    smoke_url_job_dry_run_json,
    smoke_url_job_dry_run_plans_direct_sources,
    smoke_global_json_flag_json,
    smoke_download_rejects_non_model_json,
    smoke_url_job_dry_run_rejects_non_model_before_output,
    smoke_sim_local_zip_job_json,
    smoke_sim_local_zip_long_name_json,
    smoke_local_zip_extract_error_json,
    smoke_sim_job_json,
    smoke_send_alias_json,
    smoke_sim_lower_level_json,
)


class TestAgentCliSmoke(unittest.TestCase):
    def test_smoke_help_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_help_surface(pathlib.Path(tmp))

    def test_smoke_setup_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_setup_json(pathlib.Path(tmp))

    def test_smoke_setup_json_noninteractive_missing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_setup_json_noninteractive_missing_values(pathlib.Path(tmp))

    def test_smoke_setup_json_rejects_bad_access_code_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_setup_json_rejects_bad_access_code_file(pathlib.Path(tmp))

    def test_smoke_invalid_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_invalid_config_json(pathlib.Path(tmp))

    def test_smoke_parse_error_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_parse_error_json(pathlib.Path(tmp))

    def test_smoke_preflight_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_preflight_json(pathlib.Path(tmp))

    def test_smoke_local_job_dry_run_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_local_job_dry_run_json(pathlib.Path(tmp))

    def test_smoke_url_job_dry_run_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_url_job_dry_run_json(pathlib.Path(tmp))

    def test_smoke_url_job_dry_run_plans_direct_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_url_job_dry_run_plans_direct_sources(pathlib.Path(tmp))

    def test_smoke_global_json_flag_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_global_json_flag_json(pathlib.Path(tmp))

    def test_smoke_download_rejects_non_model_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_download_rejects_non_model_json(pathlib.Path(tmp))

    def test_smoke_url_job_dry_run_rejects_non_model_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_url_job_dry_run_rejects_non_model_before_output(pathlib.Path(tmp))

    def test_smoke_sim_local_zip_job_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_sim_local_zip_job_json(pathlib.Path(tmp))

    def test_smoke_sim_local_zip_long_name_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_sim_local_zip_long_name_json(pathlib.Path(tmp))

    def test_smoke_local_zip_extract_error_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_local_zip_extract_error_json(pathlib.Path(tmp))

    def test_smoke_sim_job_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_sim_job_json(pathlib.Path(tmp))

    def test_smoke_send_alias_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_send_alias_json(pathlib.Path(tmp))

    def test_smoke_sim_lower_level_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke_sim_lower_level_json(pathlib.Path(tmp))
