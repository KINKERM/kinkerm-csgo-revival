from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_keyvalue_operation_duplicates.py <keyvalue.cpp>")

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

marker = "REVIVAL_OPERATION_DUPLICATE_KEYS_V1"
if marker in text:
    print(f"[keyvalue] already patched: {path}")
    raise SystemExit(0)

old = '''        case '"':
            current = FindOrCreateSubkey(parser.ParseString());
            break;
'''

new = '''        case '"':
        {
            std::string_view keyName = parser.ParseString();

            // REVIVAL_OPERATION_DUPLICATE_KEYS_V1
            // Valve's seasonaloperations quest_mission_card stores weekly
            // cards by repeating id/name/quests/operational_points keys.
            // The stock helper's FindOrCreateSubkey collapses repeated names,
            // which leaves only one week. Preserve duplicates only for this
            // Operation structure (and repeated quest_mission_card blocks)
            // so the rest of the schema keeps its historical behavior.
            if (m_name == "quest_mission_card"
                || keyName == "quest_mission_card")
            {
                current = &m_subkeys.emplace_back(keyName);
            }
            else
            {
                current = FindOrCreateSubkey(keyName);
            }
            break;
        }
'''

if old not in text:
    raise SystemExit(
        "[keyvalue] ERROR: expected KeyValue::Parse switch block not found; "
        "refusing to patch an unknown source layout"
    )

path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
print(f"[keyvalue] patched duplicate Operation mission-card keys: {path}")
