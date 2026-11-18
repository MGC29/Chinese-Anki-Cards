import argparse
import asyncio
import csv
import hashlib
import html
import logging
import re
import shutil
import unicodedata
import zlib
from functools import lru_cache
from pathlib import Path
import edge_tts
import hanzidentifier
import jieba
import pandas as pd
import pinyin_jyutping
import requests
from pycccedict.cccedict import CcCedict
from tqdm import tqdm

jieba.setLogLevel(logging.WARNING)


# Fields of the Anki note type, IN THE ORDER of the note type.
ANKI_FIELDS = ["Hanzi", "Color", "Pinyin", "Ruby", "English",
               "Classifier", "Sound", "Usage"]
 
BASE = "https://api.tatoeba.org/v1/sentences"
DEFAULT_NOTETYPE = "Xines"
MODEL_ID = 1607392319

# --------------------------------------------------------------------------
# Helper functions 
# --------------------------------------------------------------------------

def warn(msg):
    """
    Print a warning without breaking the progress bar's line.
    """
    tqdm.write(f"Warning: {msg}")

def clean_query(value):
    """
    Make sure a value is a string and if so cleans it. 
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()

def is_hanzi(c):
    "Assess if a character is a chinese character or something else"
    return "一" <= c <= "鿿"

def hanzi_count(text):
    "Counts number of chinese characters in a string"
    return sum(is_hanzi(c) for c in text)

def format_usage(pairs):
    """
    Format example sentences so chinese and english are together.
    """
    return "<br><br>".join(
        html.escape(zh) + (f"<br><span class='comment'>{html.escape(en)}</span>" if en else "")
        for zh, en in pairs)

def is_chinese_piece(text):
    """A piece counts as Chinese if it has at least one hanzi and no more latin letters than hanzi."""
    n = hanzi_count(text)
    return n > 0 and n >= len(re.findall(r"[A-Za-z]", text))

def user_phrases_html(text, word=""):
    """
    User-supplied sentences, separated by ';' (the fullwidth '；' and newlines also work).
    Sentences should be ordered in pairs: chinese 1; english 1; chinese 2; english 2...
    If not, only that sentence is affected, the rest are still groupped. 
    """
    pieces = [p.strip() for p in re.split(r"[;；\n]", text) if p.strip()]
    pairs = []  
    for p in pieces:
        if is_chinese_piece(p):
            if pairs and not pairs[-1][1]:
                warn(f"'{word}': no translation found for '{pairs[-1][0]}'")
            pairs.append([p, ""])
        elif pairs:
            pairs[-1][1] = pairs[-1][1] + "; " + p if pairs[-1][1] else p
        else:
            warn(f"'{word}': text before the first Chinese sentence ignored: '{p}'")
    if pairs and not pairs[-1][1]:
        warn(f"'{word}': no translation found for '{pairs[-1][0]}'")
    return format_usage([tuple(x) for x in pairs])

@lru_cache(maxsize=None)
def get_pinyin_engine():
    "Start pinyin engine"
    return pinyin_jyutping.PinyinJyutping()
 
@lru_cache(maxsize=None)
def get_cedict():
    """Loading the dictionary is slow, so it is done once, not once per row"""
    return CcCedict()

# --------------------------------------------------------------------------
# Tatoeba sentences + TTS audio 
# --------------------------------------------------------------------------

def is_simplified(s):
    """
    Checks if a phrase from Tatoeba is in traditional or simplified.
    """

    if s.get("script"):                      
        return s["script"] == "Hans"
    if hanzidentifier:
        return hanzidentifier.is_simplified(s["text"])
    raise RuntimeError("No script field in response; pip install hanzidentifier")

def english_translations(s):
    out = []
    for t in s.get("translations", []):
        out.extend(t if isinstance(t, list) else [t])  
    return [t["text"] for t in out
            if t.get("lang") == "eng" and not t.get("is_unapproved")]

def get_sentences(word, min_chars=5, max_results=3, max_trans=1, max_pages=3):
    """Query Tatoeba to get sentences, following the requirements set by the user. """

    url = BASE
    params = {
        "lang": "cmn",
        "q": word,
        "is_orphan": "no",
        "is_unapproved": "no",
        "trans:lang": "eng",
        "showtrans:lang": "eng",
        "sort": "relevance",
    }
    results, seen = [], set()

    for _ in range(max_pages):
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        body = r.json()

        for s in body["data"]:
            text = s["text"]
            if s.get("is_unapproved") or s["id"] in seen or text in seen:
                continue
            if hanzi_count(text) < min_chars or not is_simplified(s):
                continue
            eng = english_translations(s)
            if not eng:                       
                continue
            seen.update([s["id"], text])
            results.append({"id": s["id"], "zh": text, "en": eng[:max_trans]})

        paging = body.get("paging", {})
        if not paging.get("has_next"):
            break
        url, params = paging["next"], None   

    return results[:max_results]

def get_audio(text, out_path, voice="zh-CN-XiaoxiaoNeural", rate="+0%"):
    """Get audio of the word from edge tts"""
    async def _run():
        await edge_tts.Communicate(text, voice, rate=rate).save(out_path)
    asyncio.run(_run())
    return out_path 

# --------------------------------------------------------------------------
# Tones, pinyin, ruby, colour
# --------------------------------------------------------------------------

TONE_MARKS = {"\u0304": 1, "\u0301": 2, "\u030c": 3, "\u0300": 4}

def tone_of(syllable):
    """Tone (1-5) of a pinyin syllable written with tone marks."""
    for ch in unicodedata.normalize("NFD", syllable):
        if ch in TONE_MARKS:
            return TONE_MARKS[ch]
    return 5 

def computed_syllables(word):
    """
    Return the pinyin of each character. 
    Whole-word is tried first to assess for different readings.
    Per-character conversion happens as a fallback.
    """
    n = hanzi_count(word)

    engine = get_pinyin_engine()
    tokens = engine.pinyin(word, tone_numbers=False, spaces=True).split()
    if len(tokens) == n:
        return tokens
    return [engine.pinyin(c, tone_numbers=False) for c in word if is_hanzi(c)]

def pinyin_html(syllables):
    """Wrap each characetr in a tone class (see .toneN in the CSS)."""
    return " ".join(f"<span class='tone{tone_of(s)}'>{html.escape(s)}</span>"
                    for s in syllables)

def ruby_html(word, syllables):
    """Each hanzi with its pinyin above it, coloured by tone."""
    it = iter(syllables)
    parts = []
    for c in word:
        if is_hanzi(c):
            syl = next(it)
            parts.append(f"<span class='tone{tone_of(syl)}'>"
                         f"<ruby>{c}<rt>{html.escape(syl)}</rt></ruby></span>")
        else:
            parts.append(html.escape(c))
    return "".join(parts)

def color_html(word, syllables):
    """The hanzi coloured by tone, no pinyin."""
    it = iter(syllables)
    parts = []
    for c in word:
        if is_hanzi(c):
            parts.append(f"<span class='tone{tone_of(next(it))}'>{c}</span>")
        else:
            parts.append(html.escape(c))
    return "".join(parts)

# --------------------------------------------------------------------------
# Dictionary 
# --------------------------------------------------------------------------
CL_RE = re.compile(r"(?:[^|\[\],]+\|)?([^|\[\],]+)\[[^\]]*\]")

def format_classifiers(cl_strings):
    """
    Identify classifiers, which in CC-CEDICT are stored as 'CL:個|个[ge4],位[wei4]'.
    """
    engine = get_pinyin_engine()
    out = []
    for s in cl_strings:
        for m in CL_RE.finditer(s):
            h = m.group(1)
            out.append(f"{h} ({engine.pinyin(h, tone_numbers=False)})")
    return html.escape(", ".join(dict.fromkeys(out)))  

def split_definitions(text):
    """';' or line breaks separate definitions; if there are none, commas do."""
    if ";" in text or "\n" in text:
        parts = re.split(r"[;\n]", text)
    else:
        parts = text.split(",")
    return [p.strip() for p in parts if p.strip()]

# --------------------------------------------------------------------------
# Building the Anki note
# --------------------------------------------------------------------------

def audio_tag(word, media_dir):
    """
    Check if a sound file exists in the media directory and if not generates it. 
    """ 
    # Hash-based name: safe characters only and unlikely to clash with other media
    name = "tts_" + hashlib.sha1(word.encode("utf-8")).hexdigest()[:12] + ".mp3"
    dest = media_dir / name
    if not dest.exists():
        get_audio(word, str(dest))
    return f"[sound:{name}]"


def build_note(row, media_dir, column_name, max_def=5, min_chars=5, max_results=3):
    # 1st make sure that we have a word. 
    word = clean_query(row[column_name])
    if word is None:
        return None
    
    # 2nd get pinyin & color 

    syllables = computed_syllables(word)          
    pinyin_field = pinyin_html(syllables)
 
    # 3rd get definitions 
    try:
        entry = get_cedict().get_entry(word)
    except KeyError:
        entry = None

    if not entry:
        warn(f"'{word}' not found in CC-CEDICT.")
    
    raw_defs = (entry or {}).get("definitions", [])
    classifiers = [d[3:] for d in raw_defs if d.startswith("CL:")]
    meanings = [d for d in raw_defs if not d.startswith("CL:")]
 
    given_def = clean_query(row.get("Definition"))
    if given_def:
        english = "<br>".join(html.escape(d) for d in split_definitions(given_def))
    else:
        english = "<br>".join(html.escape(d) for d in meanings[:max_def])
 
    # 4th get audio 
    try:
        sound = audio_tag(word, media_dir)
    except Exception as e:
        warn(f"no audio for '{word}' ({e}).")
        sound = ""
 
    # 5th get example sentences
    given_phrase = clean_query(row.get("Phrase"))
    if given_phrase:
        usage = user_phrases_html(given_phrase, word)
    else:
        try:
            sents = get_sentences(word, min_chars=min_chars, max_results=max_results)
        except Exception as e:
            warn(f"no sentences for '{word}' ({e}).")
            sents = []
        usage = format_usage([(s["zh"], s["en"][0]) for s in sents])
 
    return {
        "Hanzi": html.escape(word),
        "Color": color_html(word, syllables),
        "Pinyin": pinyin_field,
        "Ruby": ruby_html(word, syllables),
        "English": english,
        "Classifier": format_classifiers(classifiers),
        "Sound": sound,
        "Usage": usage,
    }

# --------------------------------------------------------------------------
# Output: Anki text import file and .apkg
# --------------------------------------------------------------------------

def write_tsv(notes, path, notetype, deck):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("#separator:tab\n#html:true\n")
        f.write(f"#notetype:{notetype}\n#deck:{deck}\n")
        f.write("#columns:" + "\t".join(ANKI_FIELDS) + "\n")
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        for n in notes:
            w.writerow([n[k] for k in ANKI_FIELDS])


SPECIAL_FIELDS = {"FrontSide", "Deck", "Subdeck", "Card", "Tags", "Type", "CardFlag"}
 
def sanitize_template(tpl, fields):
    """
    Drop references to fields the note type does not have (Anki shows an error
    on every card if a template uses a field that doesn't exist).
    """
    known = set(fields) | SPECIAL_FIELDS
 
    def block(m):
        kind, name, inner = m.group(1), m.group(2).strip(), m.group(3)
        if name in known:
            return m.group(0)
        return "" if kind == "#" else inner
 
    tpl = re.sub(r"\{\{([#^])([^}]+)\}\}(.*?)\{\{/\2\}\}", block, tpl, flags=re.S)
 
    def ref(m):
        return m.group(0) if m.group(2).strip() in known else ""
 
    return re.sub(r"\{\{(?![#^/])((?:[^}:]+:)*)([^}:]+)\}\}", ref, tpl)
 
def parse_template(path):
    """Read the front/back/CSS blocks out of anki.txt."""
    text = Path(path).read_text(encoding="utf-8")
    blocks = dict(re.findall(
        r"## (Front Template|Back Template|Styling)\s*```\w*\n(.*?)```", text, re.S))
    front, back, css = blocks["Front Template"], blocks["Back Template"], blocks["Styling"]
    new_front, new_back = (sanitize_template(front, ANKI_FIELDS),
                           sanitize_template(back, ANKI_FIELDS))
    # if (new_front, new_back) != (front, back):
    #     print("Note: the template referenced fields your note type doesn't have "
    #           "(e.g. Also Written / Frequency); those parts were removed.")
    return new_front, new_back, css
 
def write_apkg(notes, path, deck, notetype, template_path, media_dir, tags):
    try:
        import genanki
    except ImportError:
        print("Error: --apkg needs genanki (pip install genanki). The TSV was still written.")
        return False
    front, back, css = parse_template(template_path)
    model = genanki.Model(
        MODEL_ID, notetype,
        fields=[{"name": f} for f in ANKI_FIELDS],
        templates=[{"name": "Card 1", "qfmt": front, "afmt": back}],
        css=css)
    # Deck id comes from the deck NAME, so every package with the same --deck
    # lands in the same deck when imported.
    deck_obj = genanki.Deck(zlib.crc32(deck.encode("utf-8")) & 0x7FFFFFFF, deck)
    media = set()
    for n in notes:
        deck_obj.add_note(genanki.Note(
            model=model, fields=[n[k] for k in ANKI_FIELDS], tags=tags,
            guid=genanki.guid_for(n["Hanzi"])))  # stable id -> re-import updates
        m = re.search(r"\[sound:(.+?)\]", n["Sound"])
        if m and (media_dir / m.group(1)).is_file():
            media.add(str(media_dir / m.group(1)))
    package = genanki.Package(deck_obj)
    package.media_files = sorted(media)
    package.write_to_file(path)
    return True

# --------------------------------------------------------------------------
# Memory: words that were already generated in previous runs
# --------------------------------------------------------------------------

MEMORY_COLUMN = "Word"

def load_memory(path):
    """
    Read the memory CSV. Returns (set of known words, dataframe, name of the word column).
    A missing or empty file is fine: it just means nothing is known yet.
    Any other read problem raises, so we never overwrite a file we couldn't understand.
    """
    empty = pd.DataFrame(columns=[MEMORY_COLUMN])
    if not path.is_file():
        print(f"Warning: memory file '{path}' does not exist, it will be created.")
        return set(), empty, MEMORY_COLUMN
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return set(), empty, MEMORY_COLUMN

    # Use the "Word" column, or the first column if the file has no such header
    col = MEMORY_COLUMN if MEMORY_COLUMN in df.columns else df.columns[0]
    words = {w.strip() for w in df[col] if w.strip()}
    return words, df, col

def save_memory(path, df, col, new_words):
    """Append the new words to the memory file (other columns are kept, left empty)."""
    new_rows = pd.DataFrame({col: new_words})
    out = new_rows if df.empty else pd.concat([df, new_rows], ignore_index=True).fillna("")
    out.to_csv(path, index=False, encoding="utf-8")

# --------------------------------------------------------------------------

def main():
    here = Path(__file__).resolve().parent
    # Read arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", required=True,
                        help="CSV file with a Word column (and optional extra columns)")
    parser.add_argument("-o", "--output", default=str(here), help="Output directory")
    parser.add_argument("-c", "--column", default="Word", help="Name of the column that contains the characters.")
    parser.add_argument("-m", "--memory", default=None, help="CSV file (a 'Word' column) with words generated in previous runs. They are skipped, and the new words are appended at the end. Created if it does not exist.")
    parser.add_argument("--deck", default="Chinese Vocabulary",
                        help="Deck name. Use the SAME name for every package to merge them")
    parser.add_argument("--notetype", default=DEFAULT_NOTETYPE, help="Note type name")
    parser.add_argument("--tags", nargs="*", default=[],
                        help="Tags added to every note (e.g. --tags HSK3 week1)")
    parser.add_argument("--no-apkg", action="store_true", help="Only write the TSV")
    parser.add_argument("--template", default=str(here / "card_template.txt"),
                        help="anki.txt with the card templates")
    parser.add_argument("--max-def", type=int, default=5, help="Max definitions per word")
    parser.add_argument("--min-char", type=int, default=5, help="Minimum characters in a Tatoeba sentence to be used as an example")
    parser.add_argument("--max-sent", type=int, default=3, help="Max number of Tatoeba sentences")

    args = parser.parse_args()

    # Check that input and output exist
    input_file, output_dir, column_name = Path(args.input), Path(args.output), args.column
    if not input_file.is_file():
        print(f"Error: Input file '{input_file}' does not exist.")
        return
    if not output_dir.is_dir():
        print(f"Error: Output directory '{output_dir}' does not exist.")
        return

    # Check that input is a csv 
    try:
        data = pd.read_csv(input_file, dtype=str)
    except Exception as e:
        print(f"Error: Input file could not be read as CSV ({e})")
        return

    # Check that the memory file exist
    memory_path = Path(args.memory) if args.memory else None
    known_words, memory_df, memory_col = set(), None, MEMORY_COLUMN
    if memory_path is not None:
        if not memory_path.resolve().parent.is_dir():
            print(f"Error: the folder of the memory file '{memory_path}' does not exist.")
            return
        try:
            known_words, memory_df, memory_col = load_memory(memory_path)
        except Exception as e:
            print(f"Error: Memory file could not be read as CSV ({e})")
            return

    # That it contains the required column
    if column_name not in data.columns:
        print(f"Error: Characters to search must be under the {column_name} column.")
        return
        
    skipped = [c for c in data.columns if c not in [column_name, "Definition", "Phrase"]]
    if skipped:
        print("Warning: columns " + ", ".join(skipped) + " will be skipped.")
 
    # Check that the media directory exists. 
    media_dir = output_dir / "media"
    media_dir.mkdir(exist_ok=True)

    # Build a note for each word in the csv. 
    notes, new_words = [], []

    rows = list(data.iterrows())
    bar = tqdm(rows, desc="Building cards", unit="word")

    for idx, row in bar:
        word = clean_query(row[column_name])
        if word is None:
            continue

        bar.set_postfix_str(word)

        if word in known_words:
            tqdm.write(f"Skipping '{word}': it has already been generated.")
            continue

        try:
            note = build_note(row, media_dir, column_name ,args.max_def, args.min_char, args.max_sent)
        except Exception as e:
            warn(f"row {idx + 2} ('{row.get(column_name)}') failed ({e}); skipped.")
            continue
        if note:
            notes.append(note)
            new_words.append(word)
            known_words.add(word)

    if not notes:
        print("No new words to generate. Nothing was written.")
        return

    # Output files are named after the input so several batches don't overwrite each other
    stem = input_file.stem
    tsv_path = output_dir / f"{stem}_anki_import.txt"
    write_tsv(notes, tsv_path, args.notetype, args.deck)
    print(f"Wrote {len(notes)} notes to {tsv_path}")

    packaged = True
    if not args.no_apkg:
        apkg_path = output_dir / f"{stem}.apkg"
        packaged = write_apkg(notes, apkg_path, args.deck, args.notetype,
                   args.template, media_dir, [t.replace(" ", "_") for t in args.tags])
        if packaged:    
            print(f"Wrote {apkg_path}")

    if memory_path is not None:
        if packaged:
            save_memory(memory_path, memory_df, memory_col, new_words)
            print(f"Added {len(new_words)} words to memory file {memory_path}")
        else:
            print("Memory file NOT updated because the .apkg could not be created.")


if __name__ == "__main__":
    main()


