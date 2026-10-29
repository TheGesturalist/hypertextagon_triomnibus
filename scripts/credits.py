"""Parse curated Grist credit strings into (name, role) pairs.
Roles: author, editor, translator, contributor. Overrides cover strings the rules cannot parse safely."""
import re

OVERRIDES = {
    349: [("Margaret Cavendish", "author")],
    11327: [
        ("Araki Yasusada", "author"),
        ("Kent Johnson", "contributor"),
        ("Tosa Motokiyu", "translator"),
        ("Ojiu Norinaga", "translator"),
        ("Okura Kyojin", "translator"),
    ],
    10984: [("Miguel Molina Alarcón", "editor"), ("Arseny Avraamov", "author")],
    6896: [
        ("Philip S. Alexander", "editor"),
        ("Armin Lange", "editor"),
        ("Renate J. Pillinger", "editor"),
    ],
    7729: [
        ("Georges Bataille", "author"),
        ("Marina Galletti", "editor"),
        ("Alastair Brotchie", "editor"),
        ("Natasha Lehrer", "translator"),
        ("John Harman", "translator"),
        ("Meyer Barash", "translator"),
    ],
    4597: [("Anonymous", "author"), ("Michael J. Curley", "translator")],
    8067: [
        ("Arnold Geulincx", "author"),
        ("Samuel Beckett", "contributor"),
        ("Martin Wilson", "translator"),
    ],
    4840: [
        ("James Cook", "author"),
        ("Philip Edwards", "editor"),
        ("J. C. Beaglehole", "editor"),
    ],
    4214: [
        ("Lu Xun", "author"),
        ("Eileen J. Cheng", "editor"),
        ("Kirk A. Denton", "editor"),
    ],
    2800: [("Nikolai Leskov", "author"), ("William Edgerton", "translator")],
    14272: [
        ("Ismail Kadare", "author"),
        ("Jon Rothschild", "translator"),
        ("David Bellos", "translator"),
    ],
    1138: [
        ("Joaquim Maria Machado de Assis", "author"),
        ("William L. Grossman", "translator"),
    ],
    12603: [
        ("Walter Benjamin", "author"),
        ("Howard Eiland", "translator"),
        ("Marcus Boon", "contributor"),
    ],
    3169: [
        ("Franz Kafka", "author"),
        ("Max Brod", "editor"),
        ("Joseph Kresh", "translator"),
        ("Martin Greenberg", "translator"),
        ("Hannah Arendt", "translator"),
    ],
    # Volume 2 — Elias Canetti Shadow week overrides
    14938: [("Elias Canetti", "author"), ("Carol Stewart", "translator")],
    413: [("Elias Canetti", "author"), ("Joachim Neugroschel", "translator")],
    415: [("Elias Canetti", "author"), ("John Hargraves", "translator")],
    401: [
        ("Karl Kraus", "author"),
        ("Jonathan Franzen", "translator"),
        ("Paul Reitter", "contributor"),
        ("Daniel Kehlmann", "contributor"),
    ],
}

ROLE_WORDS = [
    ("ed./trans.", "translator"),
    ("ed./tr.", "translator"),
    ("tr. and annotated by", "translator"),
    ("trans.", "translator"),
    ("tr.", "translator"),
    ("eds.", "editor"),
    ("ed.", "editor"),
    ("notes by", "contributor"),
    ("foreword by", "contributor"),
    ("intro.", "contributor"),
    ("preface by", "contributor"),
    ("photographs by", "contributor"),
    ("with ", "contributor"),
]


def split_names(s):
    s = s.replace(" et al.", "")
    parts = re.split(r",\s*|\s+and\s+|\s*&\s*", s)
    return [p.strip() for p in parts if p.strip()]


def parse(cid, credit):
    if cid in OVERRIDES:
        return OVERRIDES[cid]
    credit = (credit or "").strip()
    if not credit:
        return [("Unknown", "author")]
    out = []
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", credit)
    main, paren = (m.group(1), m.group(2)) if m else (credit, "")
    segs = re.split(r",\s*(?=(?:ed\.|trans\.|tr\.))", main)
    main, extra = segs[0], segs[1:]
    paren_segs = [re.sub(r"^ch\.\s*\d+\s+", "", p.strip()) for p in re.split(r";", paren) if p.strip()] + extra
    main_role = "author"
    if paren.strip() in ("ed.", "eds."):
        main_role, paren_segs = "editor", []
    for n in split_names(main):
        out.append((n, main_role))
    for seg in paren_segs:
        for word, role in ROLE_WORDS:
            if seg.startswith(word):
                for n in split_names(seg[len(word) :]):
                    out.append((n, role))
                break
        else:
            out.append(("?" + seg, "unparsed"))
    return out
