import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cpa_xai import mint


class CpaMintFlowTests(unittest.TestCase):
    def test_prefers_pkce_mint_over_device_flow_when_sso_available(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(
                    mint,
                    "mint_with_sso_pkce",
                    return_value={
                        "access_token": "not.a.jwt",
                        "refresh_token": "refresh",
                        "id_token": "",
                        "expires_in": 21600,
                        "mint_method": "pkce",
                    },
                ) as pkce,
                mock.patch.object(mint, "mint_with_sso_protocol") as device,
                mock.patch.object(mint, "probe_models", return_value={"has_grok_45": True}),
            ):
                result = mint.mint_and_export(
                    email="user@example.com",
                    password="password",
                    auth_dir=Path(td),
                    sso="sso-cookie",
                    probe=False,
                    prefer_protocol=True,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["mint_method"], "pkce")
        pkce.assert_called_once()
        device.assert_not_called()

    def test_does_not_fall_back_to_device_flow_by_default_after_pkce_failure(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(mint, "mint_with_sso_pkce", side_effect=mint.PKCEMintError("pkce denied")),
                mock.patch.object(mint, "mint_with_sso_protocol") as device,
            ):
                result = mint.mint_and_export(
                    email="user@example.com",
                    password="password",
                    auth_dir=Path(td),
                    sso="sso-cookie",
                    prefer_protocol=True,
                    force_standalone=True,
                )

        self.assertFalse(result["ok"])
        self.assertIn("pkce denied", result["error"])
        device.assert_not_called()

    def test_can_select_device_flow_directly_when_sso_available(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(mint, "mint_with_sso_pkce") as pkce,
                mock.patch.object(
                    mint,
                    "mint_with_sso_protocol",
                    return_value={
                        "access_token": "not.a.jwt",
                        "refresh_token": "refresh",
                        "id_token": "",
                        "expires_in": 21600,
                        "mint_method": "protocol",
                    },
                ) as device,
            ):
                result = mint.mint_and_export(
                    email="user@example.com",
                    password="password",
                    auth_dir=Path(td),
                    sso="sso-cookie",
                    probe=False,
                    prefer_protocol=True,
                    protocol_flow="device",
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["mint_method"], "protocol")
        pkce.assert_not_called()
        device.assert_called_once()

    def test_rejects_unknown_protocol_flow(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(mint, "mint_with_sso_pkce") as pkce,
                mock.patch.object(mint, "mint_with_sso_protocol") as device,
            ):
                result = mint.mint_and_export(
                    email="user@example.com",
                    password="password",
                    auth_dir=Path(td),
                    sso="sso-cookie",
                    probe=False,
                    prefer_protocol=True,
                    protocol_flow="bad-flow",
                )

        self.assertFalse(result["ok"])
        self.assertIn("unsupported cpa_protocol_flow", result["error"])
        pkce.assert_not_called()
        device.assert_not_called()


if __name__ == "__main__":
    unittest.main()
