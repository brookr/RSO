import subprocess
import unittest
from unittest.mock import patch

from vendor.docchain.attestation import (
    attest_doc_calldata,
    doc_block_hash_payload,
    doc_block_hash_with_cast,
    prepare_attestation,
    redact_secret_values,
    signed_attestation,
    sign_prepared_with_cast,
    typed_data_for_attestation,
)


class DocchainAttestationHelpersTest(unittest.TestCase):
    def test_typed_data_shape_includes_on_behalf_of(self):
        attestation = sample_attestation()

        typed_data = typed_data_for_attestation(
            chain_id=1,
            contract_address="0x" + "aa" * 20,
            attestation=attestation,
        )

        self.assertEqual(typed_data["domain"]["name"], "Doc Chain")
        self.assertEqual(typed_data["domain"]["version"], "1")
        self.assertEqual(typed_data["message"], attestation)
        self.assertIn(
            {"name": "onBehalfOf", "type": "address"},
            typed_data["types"]["DocAttestation"],
        )

    def test_prepare_attestation_normalizes_and_defaults(self):
        prepared = prepare_attestation(
            chain_id=1,
            contract_address="0X" + "AA" * 20,
            attester="0X" + "11" * 20,
            on_behalf_of="0X" + "22" * 20,
            doc_chain_id="0X" + "33" * 32,
            doc_ref=20260528000000,
            parent_hash="0x" + "44" * 32,
            content_hash="0x" + "55" * 32,
            uri="",
            deadline=123,
        )

        self.assertEqual(prepared["contractAddress"], "0x" + "aa" * 20)
        self.assertEqual(prepared["attestation"]["attester"], "0x" + "11" * 20)
        self.assertEqual(prepared["attestation"]["onBehalfOf"], "0x" + "22" * 20)
        self.assertEqual(prepared["typedData"]["domain"]["chainId"], 1)

    def test_signed_attestation_normalizes_signature(self):
        signed = signed_attestation({"example": True}, "0XABCD")
        self.assertEqual(signed["signature"], "0xabcd")

    def test_attest_doc_calldata_matches_expected_encoding(self):
        self.assertEqual(
            attest_doc_calldata(sample_attestation(), "0x1234"),
            "0xd2b85e96"
            "0000000000000000000000000000000000000000000000000000000000000040"
            "0000000000000000000000000000000000000000000000000000000000000160"
            "0000000000000000000000001111111111111111111111111111111111111111"
            "0000000000000000000000002222222222222222222222222222222222222222"
            "3333333333333333333333333333333333333333333333333333333333333333"
            "0000000000000000000000000000000000000000000000000000126d45930c00"
            "4444444444444444444444444444444444444444444444444444444444444444"
            "5555555555555555555555555555555555555555555555555555555555555555"
            "0000000000000000000000000000000000000000000000000000000000000100"
            "000000000000000000000000000000000000000000000000000000000000007b"
            "0000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000002"
            "1234000000000000000000000000000000000000000000000000000000000000",
        )

    def test_doc_block_hash_payload_matches_contract_hash_struct_input(self):
        self.assertEqual(
            doc_block_hash_payload(sample_attestation()["docBlock"]),
            "0xb84212102d711af6fc7ae9fa3e37753befb8b25762a552631b0e9ff9e8d07894"
            "3333333333333333333333333333333333333333333333333333333333333333"
            "0000000000000000000000000000000000000000000000000000126d45930c00"
            "4444444444444444444444444444444444444444444444444444444444444444"
            "5555555555555555555555555555555555555555555555555555555555555555",
        )

    def test_doc_block_hash_with_cast_normalizes_output(self):
        def fake_run(command, check, capture_output, text):
            self.assertEqual(command[1], "keccak")
            return subprocess.CompletedProcess(command, 0, stdout="0X" + "AB" * 32 + "\n", stderr="")

        self.assertEqual(
            doc_block_hash_with_cast(sample_attestation()["docBlock"], run=fake_run),
            "0x" + "ab" * 32,
        )

    def test_redaction_includes_disposable_and_sweeper_keys(self):
        with patch.dict(
            "os.environ",
            {
                "DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY": "0xabc",
                "RSO_SWEEPER_PRIVATE_KEY": "0xdef",
            },
            clear=False,
        ):
            self.assertEqual(redact_secret_values("x 0xabc y 0xdef"), "x <redacted> y <redacted>")

    def test_subprocess_error_detail_redacts(self):
        from vendor.docchain.attestation import subprocess_error_detail

        error = subprocess.CalledProcessError(1, ["cast"], stderr="failed 0xabc")
        with patch.dict("os.environ", {"DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY": "0xabc"}, clear=False):
            self.assertEqual(subprocess_error_detail(error), "failed <redacted>")

    def test_sign_prepared_checks_private_key_address(self):
        calls = []

        def fake_run(command, check, capture_output, text):
            calls.append(command)
            if command[1:3] == ["wallet", "address"]:
                return subprocess.CompletedProcess(command, 0, stdout="0x1111111111111111111111111111111111111111\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="0x1234\n", stderr="")

        prepared = prepare_attestation(
            chain_id=1,
            contract_address="0x" + "aa" * 20,
            attester="0x" + "11" * 20,
            doc_chain_id="0x" + "33" * 32,
            doc_ref=7,
            parent_hash="0x" + "44" * 32,
            content_hash="0x" + "55" * 32,
            deadline=123,
        )

        with patch.dict("os.environ", {"PRIVATE_KEY": "0xabc"}, clear=False):
            self.assertEqual(sign_prepared_with_cast(prepared, run=fake_run), "0x1234")

        self.assertEqual(calls[0][1:3], ["wallet", "address"])
        self.assertEqual(calls[1][1:3], ["wallet", "sign"])

    def test_sign_prepared_rejects_private_key_address_mismatch(self):
        def fake_run(command, check, capture_output, text):
            return subprocess.CompletedProcess(command, 0, stdout="0x9999999999999999999999999999999999999999\n", stderr="")

        prepared = prepare_attestation(
            chain_id=1,
            contract_address="0x" + "aa" * 20,
            attester="0x" + "11" * 20,
            doc_chain_id="0x" + "33" * 32,
            doc_ref=7,
            parent_hash="0x" + "44" * 32,
            content_hash="0x" + "55" * 32,
            deadline=123,
        )

        with patch.dict("os.environ", {"PRIVATE_KEY": "0xabc"}, clear=False):
            with self.assertRaisesRegex(ValueError, "does not match"):
                sign_prepared_with_cast(prepared, run=fake_run)


def sample_attestation():
    return {
        "attester": "0x" + "11" * 20,
        "onBehalfOf": "0x" + "22" * 20,
        "docBlock": {
            "docChainId": "0x" + "33" * 32,
            "docRef": 20260528000000,
            "parentHash": "0x" + "44" * 32,
            "contentHash": "0x" + "55" * 32,
        },
        "uri": "",
        "deadline": 123,
    }


if __name__ == "__main__":
    unittest.main()
