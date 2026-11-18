# Chinese Anki Card Generator

Turn a simple CSV list of Chinese words into ready-to-import Anki flashcards, complete with tone-coloured characters, pinyin, English definitions, classifiers, text-to-speech audio and example sentences.

## Repository contents

```
.
├── create_anki_cards.py   # the script
├── environment.yml        # conda environment
├── card_template.txt      # Anki card template and CSS
├── Example/
│   ├── example.csv        # example input
│   └── memory.csv         # example memory file
└── README.md
```

## Installation

You will need [conda](https://docs.conda.io/en/latest/miniconda.html) (or mamba) and an internet connection when running the script (Tatoeba and Edge TTS are online services).

```bash
git clone https://github.com/MGC29/Chinese-Anki-Cards.git
cd Chinese-Anki-Cards

conda env create -f environment.yml
conda activate anki-cards
```

## Quick start
You can try the script on the included example:

```bash
python create_anki_cards.py -i Example/example.csv -o Example/
```
For more options please chech [Parameters](#parameters).

This will create:

- `example_anki_import.txt`: Anki text-import file
- `example.apkg`: Anki package (double-click it, or use *File → Import* in Anki)
- `media/`: the generated audio files

Output files are named after the input file, so different batches don't overwrite each other.

## Input CSV format

The input CSV must have a header row and, at least, a column with the Chinese words (named `Word` by default; use `--column` to specify a different header). Two optional columns can also be included:

| Column       | Required | Description                                                                 |
|--------------|----------|-----------------------------------------------------------------------------|
| `Word`       | Yes      | The Chinese word or character(s)                                            |
| `Definition` | No       | Your own English definition(s). Overrides CC-CEDICT. Separate several with `;` or line breaks. |
| `Phrase`     | No       | Your own example sentences. Overrides Tatoeba. Sentences must be separated with `;` (the fullwidth `；` and line breaks also work) and alternate Chinese and English: `Chinese 1; English 1; Chinese 2; English 2`. |

> Any other columns are ignored.

## Parameters

The program takes the following parameters:

| Argument              | Default                 | Description |
|-----------------------|-------------------------|-------------|
| `-i`, `--input`       | *(required)*            | Input CSV file (see [Input CSV format](#input-csv-format)). |
| `-o`, `--output`      | script's folder         | Output directory (must exist before running the program). A `media/` subfolder is created inside it. |
| `-c`, `--column`      | `Word`                  | Name of the column containing the Chinese words. |
| `-m`, `--memory`      | `None`                    | Memory CSV (with a `Word` column) of words from previous runs. Those words are skipped and newly generated words are appended. Created if it doesn't exist. |
| `--deck`              | `Chinese Vocabulary`    | Deck name. Use the **same** name every time to merge batches into one deck. |
| `--notetype`          | `Xines`                 | Name of the Anki note type. |
| `--tags`              |  `None`                    | Tags added to every note, e.g. `--tags Unit 1`. Spaces within a tag become underscores. |
| `--no-apkg`           | off                     | Only write the text-import file, skip the `.apkg`. |in the same directory as the script | File with the card template. |
| `--max-def`           | `5`                     | Maximum number of CC-CEDICT definitions per word. |
| `--min-char`           | `5`                     | Minimum characters in a Tatoeba sentence to be used as an example. |
| `--max-sent`           | `3`                     | Maximum number of Tatoeba sentences per word. |

## Card template

The `.apkg` is built from `template.txt`, a Markdown file with three sections, `Front Template`, `Back Template` and `Styling`, that detail the appearance of the card when imported into Anki.

You can use your own templates as long as they use the fields: `Hanzi`, `Color`, `Pinyin`, `Ruby`, `English`, `Classifier`, `Sound`, `Usage`. Any other field wil be removed automatically, since Anki shows an error on cards that use non-existent fields.

Additioally, the generated HTML relies on CSS classes, `tone1` to `tone5` and `comment` which must be defined in the styling block.

## Other considerations

### Re-importing and merging

The deck ID is derived from the deck name, so packages with the same `--deck` land in the same deck. Additionally, each note's ID is derived from its `Hanzi`, so re-importing a word updates the existing note instead of creating a duplicate.

### Memory file

With `--memory memory.csv`, the script skips every word listed in the `Word` column (or the first column if there is no `Word` header) and appends the new words once the output has been written. Other columns in the file are preserved. If the `.apkg` cannot be created, the memory file is left untouched.

### "Complex" characters
Because the definitions, pinyin and example sentences are generated automatically, characters with multiple readings or complex uses (such as `得`) may get incomplete or misleading results. For example, CC-CEDICT may list only some of their meanings, Tatoeba may return sentences that show just one of their uses, and the pinyin is chosen automatically, so it may not match what you want to study.

For these words, it is recommended to provide your own content through the optional `Definition` and `Phrase` columns (see [Input CSV format](#input-csv-format)) and/or modify the `Pinyin` once you have imported the cards to Anki. 


## Acknowledgements and citations

This project builds on the following open-source packages and data sources:

**Python packages**

- [edge-tts](https://github.com/rany2/edge-tts): text-to-speech using Microsoft Edge's online service
- [jieba](https://github.com/fxsjy/jieba): Chinese text segmentation
- [hanzidentifier](https://github.com/tsroten/hanzidentifier): simplified/traditional identification
- [pinyin-jyutping](https://pypi.org/project/pinyin-jyutping/): pinyin conversion
- [pycccedict](https://pypi.org/project/pycccedict/): Python interface to CC-CEDICT
- [genanki](https://github.com/kerrickstaley/genanki): Anki package generation

**Data sources**

- [CC-CEDICT](https://cc-cedict.org/wiki/): community-maintained Chinese–English dictionary, licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
- [Tatoeba](https://tatoeba.org/): example sentences and translations contributed by the Tatoeba community, licensed under [CC BY 2.0 FR](https://creativecommons.org/licenses/by/2.0/fr/)

Please check each project's own license and terms of use, especially for the online services (Tatoeba, Edge TTS), before redistributing generated decks.
