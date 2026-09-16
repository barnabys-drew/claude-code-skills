"""Offline tests: python3 -m unittest test_search.py  (run from this directory)."""
import re
import unittest

import search

SEG = '''<div class="ts-segment">
  <span class="ts-name">{name}</span>
  <span class="ts-timestamp"><a href="https://youtube.com/watch?v={vid}&#038;t={s}">({ts})</a> </span>
  <span class="ts-text">{text}</span>
</div>'''

PAGE = "\n".join([
    '<h1 class="entry-title">Transcript for Guest | Lex Fridman Podcast #999</h1>',
    '<ul><li><a href="#chapter0_episode_highlight">0:00 &#8211; Inference highlight</a></li></ul>',
    '<h2 id="chapter0_episode_highlight"><span>Episode highlight</span></h2>',
    SEG.format(name="Guest", vid="AAAAAAAAAAA", s=0, ts="00:00:00", text="Inference is cheap."),
    '<h2 id="chapter1_introduction"><span>Introduction</span></h2>',
    SEG.format(name="Lex Fridman", vid="AAAAAAAAAAA", s=30, ts="00:00:30",
               text="The following is a conversation with Guest."),
    SEG.format(name="", vid="AAAAAAAAAAA", s=45, ts="00:00:45", text="Sponsors&#8217; stuff."),
    '<h2 id="chapter2_models"><span>Models</span></h2>',
    SEG.format(name="Guest", vid="AAAAAAAAAAA", s=9606, ts="02:40:06",
               text="I <b>inference</b> on Fireworks. Inferencing is cheap."),
    SEG.format(name="Lex Fridman", vid="BBBBBBBBBBB", s=9643, ts="02:40:43", text="Nice."),
])


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.segs = search.mark_highlights(search.parse_segments(PAGE))

    def test_segments_and_chapters(self):
        self.assertEqual(len(self.segs), 5)  # TOC link list is not a segment
        s = self.segs[3]
        self.assertEqual((s["speaker"], s["timestamp"], s["seconds"], s["chapter"]),
                         ("Guest", "02:40:06", 9606, "Models"))
        self.assertEqual(s["text"], "I inference on Fireworks. Inferencing is cheap.")

    def test_blank_speaker_inherits_previous(self):
        self.assertEqual(self.segs[2]["speaker"], "Lex Fridman")
        self.assertEqual(self.segs[2]["text"], "Sponsors’ stuff.")

    def test_video_id_is_most_common(self):
        self.assertEqual(search.video_id(PAGE), "AAAAAAAAAAA")
        self.assertIsNone(search.video_id("<p>no links</p>"))
        self.assertIsNone(search.deep_link(None, 5))

    def test_stem_search_skips_highlights(self):
        pats = [re.compile("inferenc", re.I)]
        hits, skipped = search.search(self.segs, pats)
        self.assertEqual([self.segs[i]["timestamp"] for i, _ in hits], ["02:40:06"])
        self.assertEqual(skipped, 1)
        i, spans = hits[0]
        words = {search.whole_word(self.segs[i]["text"], a, b) for a, b, _ in spans}
        self.assertEqual(words, {"inference", "Inferencing"})
        hits, _ = search.search(self.segs, pats, include_highlights=True)
        self.assertEqual(len(hits), 2)

    def test_plaintext_fallback(self):
        page = "<p>DHH</p><p>(02:40:06)</p><p>I inference on Fireworks.</p>"
        segs = search.parse_plaintext(page)
        self.assertEqual([(s["speaker"], s["timestamp"]) for s in segs], [("DHH", "02:40:06")])


if __name__ == "__main__":
    unittest.main()
