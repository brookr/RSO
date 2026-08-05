import urllib.parse
import unittest

from support.live_6529 import LiveSupportError, build_live_snapshot, probe_live_category


class Fake6529:
    def __init__(self, *, drift=False, bad_total=False):
        self.rep_state_calls = 0
        self.drift = drift
        self.bad_total = bad_total

    def __call__(self, url):
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/api/profiles/rso":
            return {
                "profile": {"external_id": "target-id", "handle": "RSO"},
                "consolidation": {
                    "consolidation_key": "0x" + "99" * 20,
                    "wallets": [
                        {
                            "wallet": {
                                "address": "0x" + "99" * 20,
                            }
                        }
                    ],
                },
            }
        if path == "/api/tdh_global_history":
            return {"data": [{"date": "2026-07-06", "block": 123}]}
        if path == "/api/profiles/rso/rep/ratings/received":
            self.rep_state_calls += 1
            rating = 3 if self.drift and self.rep_state_calls > 1 else 2
            return {
                "rating_stats": [
                    {
                        "category": "!node !github owner.repo",
                        "rating": rating,
                        "contributor_count": 2,
                    },
                    {"category": "unrelated", "rating": 999, "contributor_count": 1},
                ]
            }
        if path == "/api/profiles/rso/rep/ratings/by-rater":
            self.assert_query(query)
            second_rating = -2 if self.bad_total else -3
            return {
                "page": 1,
                "next": False,
                "count": 2,
                "data": [
                    {
                        "profile_id": "alice-id",
                        "handle": "alice",
                        "wallets": ["0x" + "11" * 20],
                        "consolidation_key": "0x" + "11" * 20,
                        "tdh": 1000,
                        "rating": 5,
                        "last_modified": "2026-07-06T00:00:00Z",
                    },
                    {
                        "profile_id": "bob-id",
                        "handle": "bob",
                        "wallets": ["0x" + "22" * 20],
                        "consolidation_key": "0x" + "22" * 20,
                        "tdh": 500,
                        "rating": second_rating,
                        "last_modified": "2026-07-06T00:01:00Z",
                    },
                ],
            }
        if path == "/api/tdh/nft/0x33FD426905F149f8376e227d0C9D3340AaD17aF1/1":
            wallet = self.assert_card_query(query)
            return {
                "count": 1,
                "next": False,
                "data": [self.card_tdh(wallet)],
            }
        raise AssertionError(f"unexpected URL {url}")

    def assert_query(self, query):
        assert query["given"] == ["false"]
        assert query["category"] == ["!node !github owner.repo"]

    def assert_card_query(self, query):
        assert query["sort"] == ["boosted_tdh"]
        assert query["sort_direction"] == ["DESC"]
        return query["search"][0]

    def card_tdh(self, wallet):
        tdh = 100 if wallet == "0x" + "11" * 20 else 40
        return {
            "contract": "0x33fd426905f149f8376e227d0c9d3340aad17af1",
            "token_id": 1,
            "consolidation_key": wallet,
            "balance": 1,
            "boost": 2,
            "boosted_tdh": round(tdh * 1.6),
            "tdh": tdh,
            "tdh__raw": tdh / 10,
        }


class Live6529Test(unittest.TestCase):
    def test_probe_exercises_every_rater_without_building_support(self):
        result = probe_live_category(
            identity="rso",
            category="!node !github owner.repo",
            card_token_id=1,
            api_base="https://example.test/api",
            fetcher=Fake6529(),
            workers=2,
        )

        self.assertEqual(result["raterCount"], 2)
        self.assertEqual(result["cardHolderCount"], 2)
        self.assertEqual(result["totalCardSpecificTdh"], 140)
        self.assertEqual(result["tdhBlock"], 123)

    def test_build_live_snapshot_nets_signed_rep_using_card_tdh(self):
        snapshot = build_live_snapshot(
            identity="rso",
            card_token_id=1,
            snapshot_date="2026-07-06",
            api_base="https://example.test/api",
            fetcher=Fake6529(),
            workers=2,
        )

        self.assertEqual(snapshot["schema"], "rso-tdh-support-snapshot-v1")
        self.assertEqual(snapshot["tdhSnapshot"]["block"], 123)
        self.assertEqual(snapshot["identities"]["alice-id"]["cardSpecificTdh"], 100)
        self.assertEqual(snapshot["identities"]["bob-id"]["cardSpecificTdh"], 40)
        self.assertEqual(snapshot["identities"]["alice-id"]["cardApiBoostedTdh"], 160)
        support = snapshot["operators"]["github:owner/repo"]
        self.assertEqual(support["positiveBackingTdh"], 100)
        self.assertEqual(support["negativeBackingTdh"], 40)
        self.assertEqual(support["netBackingTdh"], 60)
        self.assertEqual(support["usableBackingTdh"], 60)

    def test_live_snapshot_rejects_rep_drift(self):
        with self.assertRaisesRegex(LiveSupportError, "changed during collection"):
            build_live_snapshot(
                identity="rso",
                card_token_id=1,
                snapshot_date="2026-07-06",
                api_base="https://example.test/api",
                fetcher=Fake6529(drift=True),
            )

    def test_live_snapshot_rejects_category_total_mismatch(self):
        with self.assertRaisesRegex(LiveSupportError, "rating total mismatch"):
            build_live_snapshot(
                identity="rso",
                card_token_id=1,
                snapshot_date="2026-07-06",
                api_base="https://example.test/api",
                fetcher=Fake6529(bad_total=True),
            )


if __name__ == "__main__":
    unittest.main()
