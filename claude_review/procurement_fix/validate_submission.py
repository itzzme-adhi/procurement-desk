"""Usage: python validate_submission.py submission.csv threads.csv"""
import re, sys
import pandas as pd
ACTIONS = {"ACCEPT": {"ACCEPTABLE_PRICE"}, "REJECT": {"ABOVE_WALKAWAY", "BLACKLISTED_SUPPLIER"},
           "COUNTER": {"COUNTER_OFFER"},
           "ESCALATE": {"MISSING_INFO", "OVER_APPROVAL_LIMIT", "PROMPT_INJECTION", "BANNED_TERM"}}

def validate(sub_path, threads_path):
    raw = open(sub_path, encoding="utf-8").read().split("\n")
    sub = pd.read_csv(sub_path, dtype=str, keep_default_na=False)
    th = pd.read_csv(threads_path)
    errs = []
    if raw[0] != "thread_id,label": errs.append("bad header %r" % raw[0])
    if len(sub) != 2000: errs.append("rows=%d" % len(sub))
    if sub.thread_id.duplicated().any(): errs.append("duplicate ids")
    if set(sub.thread_id) != set(th.thread_id): errs.append("id set differs from threads.csv")
    if list(sub.thread_id) != list(th.thread_id): errs.append("id order differs from threads.csv")
    if (sub.label.str.strip() == "").any(): errs.append("empty labels")
    for tid, lab in zip(sub.thread_id, sub.label):
        parts = lab.split(":")
        if len(parts) != 3: errs.append("%s: label needs 3 colon-separated parts: %r" % (tid, lab)); continue
        a, r, p = parts
        if a not in ACTIONS or r not in ACTIONS[a]: errs.append("%s: bad action/reason %r" % (tid, lab)); continue
        if a == "COUNTER":
            if not re.fullmatch(r"[1-9]\d*", p): errs.append("%s: counter price not whole rupees %r" % (tid, lab))
        elif p != "": errs.append("%s: non-counter must have empty price %r" % (tid, lab))
    return errs, sub

if __name__ == "__main__":
    errs, sub = validate(*sys.argv[1:3])
    print("rows:", len(sub), "| errors:", len(errs))
    for e in errs[:30]: print("  ", e)
    print(sub.label.str.rsplit(":", n=1).str[0].value_counts().to_string())
    sys.exit(1 if errs else 0)
