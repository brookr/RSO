"""The backing-node roster the card extends its fall-through index rank with.

`build-nodes` publishes indexer/generated/nodes.json on the idx branch (beside the doc-chain
index). Each node carries its on-chain TDH backing (derived from the attestation index) and the
list is sorted by TDH descending -- the default rank the card opens with. The card re-validates
every entry and appends any it doesn't already know, so a new mirror joins the chain without
re-minting the card.
"""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline import snapshot


class NodeRosterTests(unittest.TestCase):
    def test_roster_shape_matches_the_cards_node_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nodes.json"
            roster = snapshot.write_node_roster(out, tdh_by_node={})
            self.assertEqual(roster["schema"], "rso-nodes-v1")
            self.assertIn("generated_at_utc", roster)
            self.assertTrue(out.exists())
            written = json.loads(out.read_text())
            self.assertEqual(written["nodes"], roster["nodes"])
            # every node carries the card's node fields plus its TDH backing
            for node in roster["nodes"]:
                self.assertEqual(set(node), {"id", "label", "repo", "node", "idx", "tdh"})
                self.assertIsInstance(node["tdh"], int)

    def test_roster_is_sorted_by_tdh_descending(self):
        nodes = (
            {"id": "a", "label": "A", "repo": "a/rso", "node": "node", "idx": "main"},
            {"id": "b", "label": "B", "repo": "b/rso", "node": "node", "idx": "main"},
            {"id": "c", "label": "C", "repo": "c/rso", "node": "node", "idx": "main"},
        )
        tdh = {"github:a/rso": 10, "github:b/rso": 999, "github:c/rso": 50}
        with tempfile.TemporaryDirectory() as tmp:
            roster = snapshot.write_node_roster(Path(tmp) / "nodes.json", nodes=nodes, tdh_by_node=tdh)
            self.assertEqual([n["repo"] for n in roster["nodes"]], ["b/rso", "c/rso", "a/rso"])
            self.assertEqual([n["tdh"] for n in roster["nodes"]], [999, 50, 10])

    def test_tdh_is_derived_from_attestation_events_by_canonical_node_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = Path(tmp) / "index.json"
            idx.write_text(json.dumps({"events": [
                {"nodeId": "github:OMPub/RSO", "nodeUsableBackingTdh": 3},
                {"nodeId": "github:ompub/rso", "nodeUsableBackingTdh": 7},   # case-folded, strongest wins
                {"claimedNodeId": "github:brookr/rso", "nodeUsableBackingTdh": 2},
                # combinedSupportTdh is the GROUP aggregate, not per-node backing -> ignored
                {"nodeId": "github:weak/rso", "nodeUsableBackingTdh": 1, "combinedSupportTdh": 999999},
                # only publication.nodeId carries the identity (mirrors the card's fallback)
                {"publication": {"nodeId": "github:pub/rso"}, "nodeUsableBackingTdh": 4},
                {"nodeUsableBackingTdh": 99},                                 # no nodeId -> ignored
            ]}))
            tdh = snapshot.node_tdh_from_attestations(idx)
            self.assertEqual(tdh["github:ompub/rso"], 7)
            self.assertEqual(tdh["github:brookr/rso"], 2)
            self.assertEqual(tdh["github:weak/rso"], 1)                       # NOT 999999
            self.assertEqual(tdh["github:pub/rso"], 4)
            self.assertEqual(snapshot.node_tdh_from_attestations(Path(tmp) / "missing.json"), {})

    def test_published_roster_core_fields_match_the_default(self):
        # the committed indexer/generated/nodes.json must carry the same nodes as NODE_ROSTER
        # (ignoring tdh + order, which are derived), so the published roster never drifts
        published = json.loads(snapshot.NODE_ROSTER_PATH.read_text())
        self.assertEqual(published["schema"], "rso-nodes-v1")
        strip = lambda ns: sorted(
            ({k: n[k] for k in ("id", "label", "repo", "node", "idx")} for n in ns),
            key=lambda n: n["id"],
        )
        self.assertEqual(strip(published["nodes"]), strip([dict(n) for n in snapshot.NODE_ROSTER]))


if __name__ == "__main__":
    unittest.main()
