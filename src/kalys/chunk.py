"""Split an article into its numbered parts.

An article is one row of text, but a long one covers several separate rules.
Article 97 of the Criminal Procedure Code is 2,697 characters: what the
detention ruling must contain, the rights that must be explained, the 45-hour
limit, the copy for the prosecutor. Embedding all of that as a single vector
averages those topics together, and the 45-hour rule - the thing people
actually ask about - is diluted until the search cannot find it.

Legal texts already come divided: each rule is a numbered part. So the chunks
do not have to be invented, only recognised.
"""

import re

# A part begins on its own line, as "1. ", "2. " and so on. Sub-items inside a
# part are written "1)", "2)" with a bracket, so they do not match this.
PART_START = re.compile(r"^\s*(\d{1,3})\.\s+(.*)$")


def split_into_parts(text: str) -> list[tuple[str, str]]:
    """Split `text` into (part number, part text) pairs.

    A line starts a new part only when its number follows the previous one.
    Requiring the sequence to run 1, 2, 3 stops a line that merely happens to
    begin with a number - a date, or a quoted amount - from cutting an article
    in the wrong place. Any text before part 1, and any line that does not
    start a part, belongs to whatever came before it.

    An article with no numbered parts comes back as one chunk whose number is
    an empty string, so callers can treat every article the same way.
    """
    chunks: list[tuple[str, list[str]]] = []
    expected = 1

    for line in text.splitlines():
        match = PART_START.match(line)
        if match and int(match.group(1)) == expected:
            chunks.append((match.group(1), [match.group(2)]))
            expected += 1
        elif chunks:
            chunks[-1][1].append(line)
        else:
            # Text before part 1: a preamble, or an article with no parts.
            chunks.append(("", [line]))

    parts = [(number, "\n".join(lines).strip()) for number, lines in chunks]
    parts = [(number, body) for number, body in parts if body]
    return parts or [("", text.strip())]


def chunk_text(title: str, part: str, body: str) -> str:
    """The text to embed for one part.

    The title goes on every chunk. On its own, part 5 of Article 97 reads as a
    rule about 45 hours with nothing saying what it is 45 hours of; with "The
    Procedure for the Detention of a Suspect" above it, the chunk carries its
    own context into the vector.
    """
    heading = f"{title} (part {part})" if title and part else title
    return f"{heading}\n\n{body}".strip()
