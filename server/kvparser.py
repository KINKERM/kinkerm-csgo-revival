"""Tolerant Valve KeyValues parser (enough for items_game.txt).

Supports:
  - quoted "tokens" and bare tokens
  - nested { } blocks
  - // line comments
  - trailing platform conditionals like [$WIN32]  (ignored)

Duplicate keys within a block are preserved by suffixing internal bookkeeping
is unnecessary here; the last value wins, which is fine for the fields we read.
Nested blocks with duplicate names are merged into a single dict.
"""

from __future__ import annotations

from typing import Union

KVValue = Union[str, dict]


class _Tokenizer:
    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.n = len(text)

    def _skip_ws_and_comments(self) -> None:
        while self.i < self.n:
            c = self.text[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "/" and self.i + 1 < self.n and self.text[self.i + 1] == "/":
                # comment to end of line
                while self.i < self.n and self.text[self.i] not in "\r\n":
                    self.i += 1
            else:
                break

    def next_token(self):
        self._skip_ws_and_comments()
        if self.i >= self.n:
            return None
        c = self.text[self.i]

        if c in "{}":
            self.i += 1
            return c

        if c == '"':
            self.i += 1
            start = self.i
            buf = []
            while self.i < self.n:
                ch = self.text[self.i]
                if ch == "\\" and self.i + 1 < self.n:
                    buf.append(self.text[self.i + 1])
                    self.i += 2
                    continue
                if ch == '"':
                    self.i += 1
                    break
                buf.append(ch)
                self.i += 1
            return ("STR", "".join(buf))

        # bare token
        start = self.i
        while self.i < self.n and self.text[self.i] not in " \t\r\n{}\"":
            self.i += 1
        return ("STR", self.text[start:self.i])


def _skip_conditional(tok: _Tokenizer) -> None:
    """If the next token is a [$PLATFORM] conditional, consume it."""
    save = tok.i
    nxt = tok.next_token()
    if isinstance(nxt, tuple) and nxt[1].startswith("["):
        return
    tok.i = save


def _parse_block(tok: _Tokenizer) -> dict:
    block: dict = {}
    while True:
        t = tok.next_token()
        if t is None or t == "}":
            return block
        if t == "{":
            # anonymous nested block; ignore key-less blocks
            _parse_block(tok)
            continue

        key = t[1]
        val_tok = tok.next_token()
        if val_tok is None:
            block[key] = ""
            return block
        if val_tok == "{":
            child = _parse_block(tok)
            existing = block.get(key)
            if isinstance(existing, dict):
                existing.update(child)
            else:
                block[key] = child
        else:
            block[key] = val_tok[1]
            _skip_conditional(tok)
    # unreachable


def parse(text: str) -> dict:
    tok = _Tokenizer(text)
    root = _parse_block_top(tok)
    return root


def _parse_block_top(tok: _Tokenizer) -> dict:
    """Top level: a sequence of  <key> { ... }  pairs."""
    root: dict = {}
    while True:
        t = tok.next_token()
        if t is None:
            return root
        if t in ("{", "}"):
            continue
        key = t[1]
        val_tok = tok.next_token()
        if val_tok == "{":
            child = _parse_block(tok)
            existing = root.get(key)
            if isinstance(existing, dict):
                existing.update(child)
            else:
                root[key] = child
        elif val_tok is None:
            root[key] = ""
            return root
        else:
            root[key] = val_tok[1]


def parse_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse(fh.read())
