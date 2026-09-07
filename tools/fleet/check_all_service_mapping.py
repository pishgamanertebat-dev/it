sheets = [
"بیل 101",
"بیل 112",
"لودر 201",
"لودر 205",
"بلدوزر 301",
"دامپتراک 401",
"دامپتراک 415",
"کامیون سهند 501",
"کامیون سهند 502",
"کامیون سهند 503",
"کامیون مایلر 510"
]


def map_sheet(name):

    parts = name.split()

    if name.startswith("بیل"):
        return "EX" + parts[-1]

    if name.startswith("لودر"):
        return "WA" + parts[-1]

    if name.startswith("دامپتراک"):
        return "HD" + str(int(parts[-1]) + 300)

    if name == "کامیون سهند 501":
        return "S1"

    if name == "کامیون سهند 502":
        return "S2"

    if name == "کامیون سهند 503":
        return "S3"

    if name == "کامیون مایلر 510":
        return "TR1"

    return None


for s in sheets:
    print(f"{s} -> {map_sheet(s)}")