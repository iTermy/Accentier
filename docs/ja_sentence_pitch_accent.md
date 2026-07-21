# Tokyo Japanese sentence-level pitch accent — engine reference

This is the knowledge base behind `server/app/languages/japanese.py`
(`group_accent_phrases`, `_phrase_accent`, conjugation rules, hints). Each rule
carries a confidence tag: **[solid]** = uncontested textbook rule (NHK accent
dictionary appendix / OJAD's rule engine / Kubozono's descriptions agree),
**[main]** = mainstream Tokyo with known variation, **[approx]** = deliberate
engine simplification. When the engine and this file disagree, this file wins —
fix the engine.

Notation: `↓` marks the accent nucleus (pitch falls right after that mora).
`[n]` is the accent number (0 = heiban/no fall; n = fall after the n-th mora).

## 1. Words in isolation [solid]

- `[0]` heiban: L H H … H — and any attached particles STAY HIGH.
- `[1]` atamadaka: H L L … L.
- `[2..n-1]` nakadaka: L H … H↓ L …
- `[n]` odaka: L H … H — identical to heiban alone; the fall lands on the
  following particle (はし↓が vs はしが).
- The initial L→H rise is phrase-initial only; inside a phrase it disappears.
- The nucleus cannot sit on a defective mora (ん, っ, ー, second vowel of a
  diphthong); it retreats to the previous mora. Devoiced moras (き/く/し/す/ち/つ/ひ/ふ
  between voiceless consonants) also repel the nucleus, usually shifting it
  one mora later or earlier depending on the word [main].

## 2. Accent phrases (アクセント句) [solid]

A sentence is spoken as accent phrases: a content word plus everything that
cliticizes onto it (particles 助詞, auxiliaries 助動詞, suffixes 接尾辞).
Each phrase has AT MOST ONE fall. Phrase-final odaka + particle realizes the
fall on the particle.

Phrase merging beyond the basic content-word rule:
- Unaccented prenominal modifiers (この・その・あの・どの, and unaccented
  な-adj + な, unaccented noun + の) typically merge with the following noun
  into ONE phrase carrying the noun's accent (このひ↓と). **[main]**
- An accented modifier keeps its own phrase; the following noun keeps its own
  accent, downstepped (い↓い ひ↓と). **[solid]**
- 接頭辞 attach forward (お+みず etc.); accent of the combination is
  lexical, often [1] or shifts — engine keeps the base word's accent. **[approx]**

## 3. Downstep / terracing (カタセシス) [solid]

Every phrase that actually contains a fall pushes the pitch register of ALL
following phrases down a step. Unaccented (heiban) phrases do NOT lower the
register. The register resets at intonation breaks: punctuation, clause
boundaries with pause (、。and quotation boundaries), and topic は followed by
a comma-scale break. New sentence = full reset.

## 4. Particles (after the host word) 

Neutral — continue the host phrase's pitch, no accent of their own **[solid]**:
が を に へ と で は も や の から(case "from") だけ しか ほど って と(quote) ながら(sim.)

Self-accenting after an UNACCENTED host (host stays flat, fall lands inside
the particle); after an accented host they are swallowed low **[main]**:
- まで → 〜ま↓で (みずま↓で; but さくら↓まで keeps noun's fall)
- など → 〜な↓ど
- ぐらい・くらい → 〜ぐ↓らい
- ばかり → 〜ば↓かり
- さえ・すら → 〜さ↓え・〜す↓ら
- こそ → 〜こ↓そ
- かしら → 〜か↓しら
- より → 〜よ↓り

の-deaccenting **[solid]**: odaka noun + の loses its accent entirely
(やま↓ → やまの[0]); likewise nakadaka nouns whose nucleus sits right before a
final defective mora (にほ↓ん → にほんの[0]). Exception: recently-devoiced
odaka words (ひしょ↓) resist. **[main]** Engine: apply the two main cases.

Sentence-final ね/よ/か/な/わ: ride the tail of the phrase; after heiban they
stay high, after a fall they stay low. か in a question gets rising boundary
tone — intonation, not accent (don't draw a new fall). **[solid]**

## 5. Copula [solid]

- だ after anything: no accent of its own (heiban noun + だ stays flat).
  だった → 〜だ↓った after unaccented host; swallowed after accented.
- です・でした・でしょう: self-accented で↓す/で↓した/でしょ↓う after an
  unaccented host (みずで↓す); swallowed low after an accented host (あ↓めです).

## 6. Verb conjugation

Verbs are binary: accented (citation fall on penultimate mora, e.g. たべ↓る,
よ↓む) or unaccented (いう, かう, ならぶ). Unidic cType distinguishes
ichidan (一段) from godan (五段).

| form | unaccented verb | accented verb |
|---|---|---|
| citation る | [0] | penult ↓ **[solid]** |
| 〜ます | 〜ま↓す (ました→ま↓した, ません→ませ↓ん, ましょう→ましょ↓う) — ALWAYS, both classes **[solid]** |
| て/た/たら/たり | [0] **[solid]** | ichidan: citation−1 (たべ↓る→た↓べて, しらべ↓る→しら↓べて); godan: same mora as citation (わか↓る→わか↓って, およ↓ぐ→およ↓いで); min [1]; retreat off defective moras (はい↓る→は↓いって) **[main]** |
| ない | [0] (いわない) **[solid]** | fall right before ない: 〜◯↓ない (たべ↓ない, わから↓ない) **[solid]** |
| なかった | 〜な↓かった (both classes: いわな↓かった, たべな↓かった) **[main]** |
| たい | [0] (かいたい) **[main]** | 〜た↓い (たべた↓い); たかった→た↓かった; たくない→た↓くない |
| ば | 〜◯↓ば on mora before ば (いえ↓ば) **[main]** | same mora as citation (たべ↓れば, よ↓めば) **[solid]** |
| volitional う/よう | penultimate: いこ↓う, たべよ↓う — both classes **[solid]** |
| れる/られる/せる/させる | chain stays in the stem's class: unaccented→[0], accented→penult of the extended citation (たべられ↓る); conjugated further, apply this table to the extended stem **[main]** |
| ている | te-accent survives, いる cliticizes low (た↓べている); unaccented → all flat (かっている); ています → 〜ていま↓す; ていた: accented host keeps te-accent, unaccented → 〜てい↓た **[main]** |
| てください | 〜てくださ↓い; accented host keeps its fall, ください swallowed **[main]** |
| そう(だ) hearsay/appearance, らしい, みたい | keep host accent; if host unaccented, そ↓う/らし↓い/みた↓い **[approx]** |

## 7. い-adjective conjugation

Accented citation = fall on penultimate (たか↓い); unaccented exist (あかい,
あまい, つめたい). いい is irregular (stem よ).

| form | unaccented adj | accented adj |
|---|---|---|
| citation い | [0] | penult ↓ **[solid]** |
| 〜く / 〜くて | [0] (あかくて) **[solid]** | citation−1: た↓かく, た↓かくて, よ↓くて **[main]** (NHK lists both た↓かくて and たか↓くて; engine uses retraction) |
| 〜かった | fall before かった: あか↓かった **[main]** | same mora as citation: たか↓かった, おいし↓かった, よ↓かった **[main]** (older Tokyo retracts: た↓かかった — both live) |
| 〜くない | 〜くな↓い **[approx]** | retracted く + ない low: た↓かくない **[main]** |
| 〜ければ | 〜け↓れば **[approx]** | same mora as citation **[main]** |
| 〜いです | adjective keeps its own accent; です swallowed (たか↓いです); unaccented adj + です → 〜いで↓す **[solid]** |

## 8. Compounds, numbers, names [main→approx]

- Compound nouns get ONE accent, usually determined by the second element
  (McCawley/Kubozono rules): short 2nd elements put the fall on the last mora
  of the first element or on the junction; long (3-4 mora) 2nd elements keep
  their own accent; some suffixes (〜語, 〜人 …) have lexical behavior
  (にほんご[0], にほんじ↓ん). The engine does NOT compute these — unidic
  usually tokenizes lexicalized compounds whole and the dictionaries list them.
- Number + counter accents are lexical per pair (いち↓じ, にじ↓? …); the
  engine trusts dictionary lookups of the fused token when available and
  otherwise leaves the number phrase unaccented rather than inventing a fall.
  **[approx — flagged in audit]**
- 〜さん/〜たち/〜くん cliticize without changing the host accent (unaccented
  host stays flat: たなかさん[0]). **[main]**

## 9. What the diagram promises the user

The sentence diagram is a *generated rule-based approximation* of a natural
Tokyo reading: per-phrase H/L rails with downstep levels and ‖ at intonation
resets. It deliberately ignores: boundary rise on questions, focus/emphasis
deaccenting, dialect, and speaker-specific phrasing of long adverbial chains.
When the deck's curated per-word accent and a dictionary disagree, the deck
wins for the target word; sentence diagrams must show the SAME fall for the
target word as the word diagram above it (after conjugation rules — a
conjugated target shows the conjugated accent, and gets a hint when the two
legitimately differ).

## 10. UniDic accent machinery (what the engine actually runs on)

unidic-lite ships per-token accent data that encodes the NHK appendix rules —
the same system Open JTalk uses. Decoded and validated against the battery in
`test_units.py::test_phrase_accent_battery`:

- `aType`: the token's own accent, FORM-SPECIFIC for conjugated stems
  (帰っ=1 は↓いって-style pre-shifted, 分かっ=2, 食べ=2, 高かっ=2) and
  comma-separated alternates for nouns (毎日="1,0" → take the first).
- `aConType`: attachment rule per host category, e.g. `動詞%F2@1,名詞%F1`.
  Interpretation (host accent `a`, host mora count `n`, argument `v`):
  - **F1** — no change.
  - **F2@v** — accented host keeps its accent; unaccented host gains a fall at
    `n+v` (です@1 → みずで↓す / あ↓めです; ば@0 → いえ↓ば / よ↓めば;
    まで・など・ばかり・より・なんて@1 after flat hosts).
  - **F3@v** — unaccented host stays flat; accented host's fall RELOCATES to
    `n+v` (られる@2 → みられ↓る; ない@0 → fall right before ない).
  - **F4@v** — always sets the fall at `n+v` (ます@1, たい@1, ん/ず@0 →
    いきませ↓ん, いか↓ず). For 助詞 (ぐらい) we soften F4 to F2: particles
    never steal from an accented host.
  - `aModeType` **M1@u** — the fall sits `u` moras from the morpheme's end
    (ましょう/でしょう/だろう/意向形 → ましょ↓う, いこ↓う).
  - `aModeType` **M2@u** — only if still unaccented, gain a fall `u` from the
    morpheme's end (なかっ@2 → かわな↓かった; 赤かっ@2 → あか↓かった).
- `C1..C5` on suffixes/attached content words: C1 = the attachment's own
  accent wins (そう → げんきそ↓う); C2/C3 = own accent only after flat hosts
  (じゃな↓い, ひと↓つ keeps host's); C4/C5 = ride the host (さん).

Engine-level overrides on top of the raw rules (each verified):
- 助動詞-タ (た/たら/だ) and 接続助詞 て/で: if the host fall sits on the
  chain's final mora and that mora belongs to a conjugating stem, retract it
  one mora (skipping ッ/ン/ー): 食べ+た → た↓べた, 見+て → み↓て,
  食べさせ+た → たべさ↓せた — but 分かっ+た → わか↓った (fall not final).
  After an unaccented host, no odaka fall is drawn (買った [0], not [3]).
- ない/なかっ/なけれ after a VERB stem: relocate to `n` (はしら↓ない even
  though the stem fall was earlier); after 助動詞 hosts keep the F3 behavior
  (いきた↓くない keeps たい's fall).
- 終助詞 (ね/よ/か/な/わ/ぞ …): always F1 — their movement is boundary
  intonation, not accent.
- だろう: treated like でしょう (みずだろ↓う), diverging from unidic's F1.
- の-deaccent (§4) applied to nominal hosts.

## Sources

- NHK日本語発音アクセント新辞典 appendix rules, as vendored in the app's
  pitch dictionaries and cross-checked against the deck's curated field.
- [Wikipedia: Japanese pitch accent](https://en.wikipedia.org/wiki/Japanese_pitch_accent)
  — particle groups, conjugation shift table, downstep, compound rules.
- [OJAD — Online Japanese Accent Dictionary](https://www.gavo.t.u-tokyo.ac.jp/ojad/)
  (Univ. of Tokyo Minematsu lab) — conjugation accents; its Suzuki-kun engine
  is the closest public analogue of what we generate.
- [Tatsumoto pitch accent primer](https://tatsumoto.neocities.org/blog/japanese-pitch-accents),
  [Migaku pitch guide](https://migaku.com/blog/japanese/japanese-pitch-accent)
  — learner-facing summaries used to sanity-check particle/copula behavior.
- Kubozono (2008, *The Phonology of Japanese*) — accent phrase formation,
  catathesis, compound accent (internal knowledge, not fetched).
