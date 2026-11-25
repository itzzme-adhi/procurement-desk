"""Usage: python run_submission.py threads.csv out.csv  [sample_answers.csv]"""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from procurement_desk.decision import decide_thread

def main(src, dst, truth=None):
    df = pd.read_csv(src)
    out = pd.DataFrame({"thread_id": df["thread_id"],
                        "label": [decide_thread(r) for _, r in df.iterrows()]})
    out.to_csv(dst, index=False, lineterminator="\n")
    if truth:
        t = pd.read_csv(truth).merge(out, on="thread_id", suffixes=("_true", "_pred"))
        ok = (t.label_true == t.label_pred)
        print(f"{ok.sum()}/{len(t)} match")
        for _, r in t[~ok].iterrows():
            print(" MISMATCH", r.thread_id, "expected", r.label_true, "got", r.label_pred)

if __name__ == "__main__":
    main(*sys.argv[1:])
