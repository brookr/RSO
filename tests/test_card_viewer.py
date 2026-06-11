"""Contract tests for the archive card viewer (card/index.html).

The card is a single-file artwork that consumes live repo artifacts. These
tests pin the contracts between the viewer and the rest of the pipeline so a
change on either side fails loudly:

- the viewer stays self-contained (vendored three.js, one inline module);
- it only fetches bundle mirrors a browser can actually read (Arweave + raw
  node-branch files — never GitHub release assets, whose redirect target sends
  no CORS headers);
- the generated attestation index keeps the shape the viewer parses;
- the witness gate defaults stay coherent between markup and script.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "card" / "index.html"
INDEX = ROOT / "indexer" / "generated" / "sepolia" / "rso-docchain-index.json"


class CardArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = CARD.read_text(encoding="utf-8")

    def test_single_file_with_vendored_three(self):
        self.assertTrue(CARD.is_file())
        self.assertEqual(self.html.count("<script"), 1, "one inline module only")
        self.assertIn('import(new URL("./three.module.js", MOUNT)', self.html)
        for vendored in ("three.module.js", "three.core.js"):
            self.assertTrue((CARD.parent / vendored).is_file(), vendored)
        # no CDN/runtime dependencies — the piece must outlive any host
        self.assertNotIn("cdn.jsdelivr.net", self.html)
        self.assertNotIn("unpkg.com", self.html)
        self.assertNotIn("esm.sh", self.html)

    def test_mount_agnostic_module_resolution(self):
        # served at a slashless path (om.pub/rso/live, Arweave gateways) a bare
        # relative import would resolve against the parent directory
        self.assertIn("const MOUNT = new URL(location.href)", self.html)
        self.assertIn('MOUNT.pathname += "/"', self.html)

    def test_bundle_mirrors_are_browser_readable(self):
        # Arweave first, then the raw node-branch catalog — and never the
        # GitHub release asset, which a browser can never read cross-origin.
        self.assertIn("dest.arweave?.transaction_url", self.html)
        self.assertIn("catalog.json.gz`", self.html)
        self.assertNotIn("releases/download", self.html)
        self.assertNotIn("asset_url", self.html)

    def test_observation_plane_is_wired(self):
        self.assertIn("annotations.json", self.html)
        self.assertIn("digestAnnotations", self.html)
        self.assertIn('"Observations"', self.html)
        # decay notices / namings / amendments each get a distinct colour
        for const in ("OBS_DECAY", "OBS_NAME", "OBS_EDIT"):
            self.assertIn(const, self.html)

    def test_witness_gate_defaults_are_coherent(self):
        # script default, persisted-settings fallback and slider markup must
        # agree: rank 0 = any sweeper-accepted witness counts
        self.assertRegex(self.html, r"ethRank:\s*0\b")
        self.assertRegex(self.html, r"s\.ethRank \?\? 0")
        slider = re.search(r'<input type="range" id="set-eth"[^>]*>', self.html)
        self.assertIsNotNone(slider)
        self.assertIn('value="0"', slider.group(0))

    def test_downloads_are_verified_on_device(self):
        # the ledger sha256 hashes the canonical catalog bytes — exactly what
        # the viewer holds after gunzip, so one digest proves the download is
        # the attested record
        self.assertIn('crypto.subtle.digest("SHA-256", bytes)', self.html)
        self.assertIn("verifyCatalogBytes(date, catBytes)", self.html)
        self.assertIn("hex === led.sha", self.html)
        self.assertIn("verified on this device", self.html)
        self.assertIn("DOES NOT MATCH DOWNLOAD", self.html)

    def test_attested_core_face_shows_consensus_hash(self):
        self.assertIn('id="fp-core"', self.html)
        self.assertIn("content_sha256", self.html)
        self.assertIn("content_schema", self.html)

    def test_contract_link_is_chain_aware(self):
        self.assertIn('id="fp-contract"', self.html)
        self.assertIn("sepolia.etherscan.io", self.html)
        self.assertIn("https://etherscan.io", self.html)
        # links inside a rotating prism must not also rotate it
        self.assertIn('closest("a")', self.html)

    def test_field_shows_whats_up_there(self):
        # re-entered objects keep their slot only as today's decay event or a
        # fresh observation; the long-gone never fly past the camera
        self.assertIn(
            '!o.reentered || o.status === "decayed" || o.anno', self.html
        )

    def test_per_type_silhouettes(self):
        self.assertIn("aShape", self.html)
        for marker in ("payload (generic) — bus + 1..6 fanned panels",
                       "rocket body — flat-ended barrel + nozzle bell",
                       "debris — every shard fractured its own way", "unknown — ring, seeded gauge"):
            self.assertIn(marker, self.html)

    def test_constellation_archetypes_are_name_keyed(self):
        # the famous constellations get their real silhouettes, in field and vignette alike
        self.assertIn("function payloadShape(", self.html)
        for marker in ('n.startsWith("STARLINK")', 'n.startsWith("ONEWEB")',
                       'n.includes("IRIDIUM")', "STARLINK — flat slab + one broad sail",
                       "ONEWEB — box-wing", "IRIDIUM — two wings + the big canted antenna",
                       "GEO comsat — long symmetric wings + dish"):
            self.assertIn(marker, self.html)

    def test_rcs_is_the_size_family(self):
        # radar cross-section dominates rendered size: tiers spaced 2.5x apart with
        # jitter too small to cross them, echoed in the vignette scale and meta line
        self.assertIn('rcs === "LARGE" ? 1.55 : rcs === "MEDIUM" ? 1.0 : rcs === "SMALL" ? 0.62', self.html)
        self.assertIn("0.92 + rand2(nid, 89) * 0.16", self.html)
        self.assertIn('rcs === "LARGE" ? 1.28', self.html)   # vignette presence
        self.assertIn("` · rcs ${m.rcs.toLowerCase()}`", self.html)

    def test_hourly_lap_and_persistent_inspector(self):
        # at rest, every object laps in exactly one hour — spot it, see it again
        self.assertIn("const LAP_SECONDS = 3600", self.html)
        self.assertIn("restRate + warpFlow * o.wj", self.html)
        # the inspector stays up until another tap or blank space — no auto-hide timer
        self.assertNotIn("5200", self.html)

    def test_generative_identity_is_norad_seeded_and_guarded(self):
        # identity seeds key to the NORAD id → same silhouette everywhere, forever
        self.assertIn("rand2(nid, 71)", self.html)
        self.assertIn('pointGeo.setAttribute("aSeed"', self.html)
        # orientation derives from real motion (previous position attribute)…
        self.assertIn('pointGeo.setAttribute("aPrev"', self.html)
        # …with the near-plane guarded and NaNs trapped before they paint the quad
        self.assertIn("step(0.05, clip.w) * step(0.05, clipP.w)", self.html)
        self.assertIn("d != d) discard", self.html)
        # real elements drive placement: inclination weave + eccentric breathing
        self.assertIn("Number(row.INCLINATION)", self.html)
        self.assertIn("o.eccV", self.html)

    def test_inspector_vignette_shares_context_and_seeds(self):
        self.assertIn("buildVigShape", self.html)
        self.assertIn("setScissorTest(true)", self.html)
        self.assertNotIn("new THREE.WebGLRenderer({ canvas: ", self.html.replace(
            'new THREE.WebGLRenderer({ canvas, antialias', ""))  # exactly one renderer/context
        self.assertIn("geometry.dispose()", self.html)

    def test_overdrive_respects_reduced_motion(self):
        self.assertIn("__HOLD_OVER__", self.html)
        self.assertRegex(self.html, r"reduced \? 0\s*:\s*smooth\(")

    def test_permanence_reads_signed_publication_locations(self):
        # each witness signs its publication locations into its attestation; the
        # card counts distinct Arweave locators as independent permanent copies
        self.assertIn('id="witness-perm"', self.html)
        self.assertIn("independent permanent copies", self.html)
        self.assertIn(r"arweave\.(net|dev)", self.html)
        # declared locations also serve as verified download mirrors
        self.assertIn("(attestByDate.get(date) || {}).ar", self.html)

    def test_embed_readiness(self):
        # fullscreen never promises what a sandboxed host forbids
        self.assertIn('id="fullscreen-btn"', self.html)
        self.assertIn("document.fullscreenEnabled", self.html)
        # motion sensors are probed via permissions policy → no console violations
        self.assertIn('allowsFeature("accelerometer")', self.html)
        # responsive HUD: compact + whisper breakpoints exist
        self.assertIn("@media (max-width: 640px), (max-height: 540px)", self.html)
        self.assertIn("@media (max-width: 420px), (max-height: 380px)", self.html)
        # boot beacon for embedding hosts
        self.assertIn('parent.postMessage({ rso: "ready"', self.html)
        # the embed test kit ships with the card
        self.assertTrue((CARD.parent / "nft-preview.html").is_file())
        self.assertTrue((CARD.parent / "serve.py").is_file())

    def test_lenses_end_with_zen(self):
        lenses = re.search(r"const LENSES = \[(.*?)\];", self.html)
        self.assertIsNotNone(lenses)
        names = re.findall(r'"([^"]+)"', lenses.group(1))
        self.assertEqual(names[-1], "Zen")
        self.assertIn("Observations", names)
        self.assertIn('LENSES.indexOf("Zen")', self.html)


class AttestationIndexContractTest(unittest.TestCase):
    """The exact fields the viewer (and om.pub pages) read from the index."""

    @classmethod
    def setUpClass(cls):
        cls.index = json.loads(INDEX.read_text(encoding="utf-8"))

    def test_chain_metadata(self):
        self.assertTrue(
            re.fullmatch(r"0x[0-9a-fA-F]{40}", self.index["contractAddress"])
        )
        self.assertIsInstance(self.index["chainId"], int)
        self.assertGreaterEqual(self.index["docRefCount"], 1)

    def test_docref_entries_carry_what_the_viewer_parses(self):
        refs = self.index["docRefs"]
        self.assertEqual(len(refs), self.index["docRefCount"])
        for ref, rec in refs.items():
            self.assertRegex(rec["date"], r"^\d{4}-\d{2}-\d{2}$", ref)
            groups = rec.get("agreementGroups")
            self.assertTrue(groups, f"{ref} has no agreement groups")
            for group in groups:
                self.assertIn("blockHash", group)
                self.assertIn("combinedSupportTdh", group)
                self.assertIsInstance(group.get("attesters"), list)
            self.assertTrue(rec.get("blockHashes"), ref)

    def test_dates_are_contiguous_daily(self):
        from datetime import date, timedelta

        dates = sorted(
            date.fromisoformat(rec["date"])
            for rec in self.index["docRefs"].values()
        )
        for previous, current in zip(dates, dates[1:]):
            self.assertEqual(
                current - previous, timedelta(days=1),
                f"gap between {previous} and {current}",
            )


if __name__ == "__main__":
    unittest.main()
