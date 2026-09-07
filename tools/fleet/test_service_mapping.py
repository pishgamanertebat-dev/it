sheets = [
    "بیل 101",
    "لودر 201",
    "دامپتراک 414",
    "کامیون سهند 501"
]


def map_sheet(name):
    parts = name.split()

    if name.startswith("بیل"):
        return "EX" + parts[-1]

    if name.startswith("دامپتراک"):
        return "HD" + str(int(parts[-1]) + 300)

    if name.startswith("لودر"):
        return "WA" + parts[-1]

    return None


for s in sheets:
    print(s, "->", map_sheet(s))