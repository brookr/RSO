import unittest

from support.tdh import (
    allocate_identity_tdh,
    build_operator_support,
    node_id_digest,
    normalize_support_record,
    parse_node_category,
)


class TdhSupportTest(unittest.TestCase):
    def test_category_commands_resolve_to_canonical_node_ids(self):
        self.assertEqual(
            parse_node_category("!node !github BrookR.RSO"),
            "github:brookr/rso",
        )
        self.assertEqual(
            parse_node_category("  !NODE   !DOMAIN  RSO.OM.PUB "),
            "domain:rso.om.pub",
        )

    def test_digest_category_requires_a_verified_roster_mapping(self):
        node_id = "github:owner/repo-with-dash"
        digest = node_id_digest(node_id)
        category = f"!node !id {digest}"

        self.assertEqual(parse_node_category(category), "")
        self.assertEqual(
            parse_node_category(category, digest_nodes={digest: node_id}),
            node_id,
        )
        self.assertEqual(
            parse_node_category(category, digest_nodes={digest: "github:wrong/repo"}),
            "",
        )

    def test_direct_categories_reject_unrepresentable_dashes(self):
        self.assertEqual(parse_node_category("!node !github owner.repo-with-dash"), "")
        self.assertEqual(parse_node_category("!node !domain node-name.example"), "")

    def test_signed_allocation_preserves_exact_absolute_tdh_budget(self):
        allocation = allocate_identity_tdh(
            10,
            {
                "github:a/one": 1,
                "github:b/two": -1,
                "github:c/three": 1,
            },
        )

        self.assertEqual(
            allocation,
            {
                "github:a/one": 4,
                "github:b/two": -3,
                "github:c/three": 3,
            },
        )
        self.assertEqual(sum(abs(amount) for amount in allocation.values()), 10)

    def test_operator_support_nets_upvotes_and_downvotes(self):
        operators = build_operator_support(
            {
                "alice": {
                    "cardSpecificTdh": 100,
                    "nodeRep": {
                        "!node !github owner.good": 3,
                        "!node !github owner.bad": -1,
                    },
                },
                "bob": {
                    "cardSpecificTdh": 40,
                    "nodeRep": {"!node !github owner.bad": -5},
                },
            }
        )

        self.assertEqual(operators["github:owner/good"]["usableBackingTdh"], 75)
        bad = operators["github:owner/bad"]
        self.assertEqual(bad["positiveBackingTdh"], 0)
        self.assertEqual(bad["negativeBackingTdh"], 65)
        self.assertEqual(bad["netBackingTdh"], -65)
        self.assertEqual(bad["usableBackingTdh"], 0)
        self.assertEqual(bad["negativeBackerCount"], 2)

    def test_support_record_rejects_derived_field_mismatch(self):
        with self.assertRaisesRegex(ValueError, "netBackingTdh"):
            normalize_support_record(
                {
                    "positiveBackingTdh": 10,
                    "negativeBackingTdh": 2,
                    "netBackingTdh": 10,
                    "usableBackingTdh": 10,
                }
            )


if __name__ == "__main__":
    unittest.main()
