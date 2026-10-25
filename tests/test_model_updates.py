import unittest
from unittest.mock import Mock, patch

import requests

import cull
import model_updates
from model_config import (
    AESTHETIC_MODEL_REVISION,
    CLIP_MODEL_REVISION,
    MUSIQ_WEIGHTS_SHA256,
)
from model_updates import ModelUpdate, ModelUpdateCheck, check_model_updates


def response(*, json=None, headers=None, status=200) -> Mock:
    reply = Mock()
    reply.json.return_value = json
    reply.headers = headers or {}
    if status >= 400:
        reply.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    return reply


def fake_get(clip_sha=CLIP_MODEL_REVISION, pinned_commit="a" * 40, latest_commit="a" * 40):
    def get(url, params=None, **kwargs):
        if url == model_updates.CLIP_REVISION_URL:
            return response(json={"sha": clip_sha})
        if params.get("sha") == AESTHETIC_MODEL_REVISION:
            return response(json=[{"sha": pinned_commit}])
        return response(json=[{"sha": latest_commit}])

    return get


def fake_head(sha=MUSIQ_WEIGHTS_SHA256):
    return lambda url, **kwargs: response(headers={"X-Linked-ETag": f'"{sha}"'})


class CheckModelUpdatesTests(unittest.TestCase):
    def test_current_models_report_nothing(self) -> None:
        with patch("requests.get", fake_get()), patch("requests.head", fake_head()):
            result = check_model_updates()
        self.assertEqual(result.updates, ())
        self.assertEqual(result.unavailable, {})
        self.assertEqual(result.to_dict()["status"], "current")

    def test_each_changed_model_is_reported(self) -> None:
        with (
            patch("requests.get", fake_get(clip_sha="b" * 40, latest_commit="c" * 40)),
            patch("requests.head", fake_head(sha="d" * 64)),
        ):
            result = check_model_updates()
        self.assertEqual(
            {update.name for update in result.updates}, {"CLIP", "MUSIQ", "aesthetic head"}
        )
        self.assertEqual(result.to_dict()["status"], "updates_available")

    def test_failed_lookups_are_recorded_not_raised(self) -> None:
        for error in (
            requests.ConnectionError("offline"),
            requests.Timeout("slow"),
            requests.HTTPError("403 rate limit"),
        ):
            with self.subTest(error=type(error).__name__):
                with (
                    patch("requests.get", Mock(side_effect=error)),
                    patch("requests.head", Mock(side_effect=error)),
                ):
                    result = check_model_updates()
                self.assertEqual(result.updates, ())
                self.assertEqual(set(result.unavailable), {"CLIP", "MUSIQ", "aesthetic head"})
                self.assertEqual(result.to_dict()["status"], "incomplete")

    def test_unexpected_response_shape_is_recorded(self) -> None:
        with (
            patch("requests.get", lambda *a, **k: response(json={})),
            patch("requests.head", lambda *a, **k: response(headers={})),
        ):
            result = check_model_updates()
        self.assertEqual(set(result.unavailable), {"CLIP", "MUSIQ", "aesthetic head"})

    def test_one_failed_lookup_does_not_hide_another_update(self) -> None:
        with (
            patch("requests.get", fake_get(clip_sha="b" * 40)),
            patch("requests.head", Mock(side_effect=requests.ConnectionError("offline"))),
        ):
            result = check_model_updates()
        self.assertEqual([update.name for update in result.updates], ["CLIP"])
        self.assertEqual(set(result.unavailable), {"MUSIQ"})


class StartupReportTests(unittest.TestCase):
    def audit(self) -> Mock:
        audit = Mock()
        audit.data = {}
        return audit

    def config(self) -> Mock:
        config = Mock()
        config.plain = True
        return config

    def test_notice_names_models_and_says_pins_are_kept(self) -> None:
        check = ModelUpdateCheck((ModelUpdate("CLIP", "a" * 40, "b" * 40),), {})
        audit = self.audit()
        printed = Mock()
        with (
            patch("model_updates.check_model_updates", return_value=check),
            patch.object(cull, "console", printed),
        ):
            cull._report_model_updates(self.config(), audit)
        messages = " ".join(call.args[0] for call in printed.print.call_args_list)
        self.assertIn("CLIP", messages)
        self.assertIn("still uses the pinned models", messages)
        self.assertEqual(audit.data["model_update_check"]["status"], "updates_available")

    def test_failed_lookup_prints_one_line_and_is_recorded(self) -> None:
        check = ModelUpdateCheck((), {"MUSIQ": "SSLError: certificate verify failed"})
        audit = self.audit()
        printed = Mock()
        with (
            patch("model_updates.check_model_updates", return_value=check),
            patch.object(cull, "console", printed),
        ):
            cull._report_model_updates(self.config(), audit)
        lines = [
            call.args[0]
            for call in printed.print.call_args_list
            if call.args[0] != "Checking for newer models"
        ]
        self.assertEqual(len(lines), 1)
        self.assertIn("Could not check for newer models:", lines[0])
        self.assertIn("MUSIQ (SSLError)", lines[0])
        self.assertNotIn("certificate verify failed", lines[0])
        self.assertEqual(audit.data["model_update_check"]["status"], "incomplete")

    def test_current_models_print_no_notice(self) -> None:
        audit = self.audit()
        printed = Mock()
        with patch.object(cull, "console", printed):
            cull._report_model_updates(self.config(), audit)
        messages = " ".join(call.args[0] for call in printed.print.call_args_list)
        self.assertNotIn("Newer upstream", messages)
        self.assertEqual(audit.data["model_update_check"]["status"], "current")


class OptionTests(unittest.TestCase):
    def test_check_is_on_by_default(self) -> None:
        args = cull.build_parser().parse_args(["photos"])
        self.assertTrue(cull.config_from_args(args).check_model_updates)

    def test_flag_turns_check_off(self) -> None:
        args = cull.build_parser().parse_args(["photos", "--no-model-update-check"])
        self.assertFalse(cull.config_from_args(args).check_model_updates)


if __name__ == "__main__":
    unittest.main()
