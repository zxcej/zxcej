#!/usr/bin/env python3
"""Public ICLR27 high-score theory pilot; adapted to in-submission stage.

Inspired by openreview_downloader and shiqiangw/iclr-2027-scores.
Never scrapes private pages or uses credentials. Existing papers.json is
public and source-authored; live OpenReview PDF access is best effort.
"""
import csv, hashlib, importlib.metadata, json, math, re, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from pathlib import Path

OUT=Path("data/iclr27_probe"); OUT.mkdir(parents=True,exist_ok=True)
BASE="https://raw.githubusercontent.com/shiqiangw/iclr-2027-scores/main/docs/data/"
THEORY=re.compile(r"(converg|regret|minimax|sample complex|oracle|optimal|complexity|generaliz|provabl|proof|rates|stochastic|gradient|optimization|convex|nonconvex|game|distribution shift|bandit|PAC learning|theory of|theoretical|spectral|learning rate|bilevel|online learning|causal|robust learn|submodular|diffusion sampling|sample efficient|representation learning|transfer learning|identifiab|decision process)",re.I)
STOP=set("the and from for with into under without over via using use based on to in of a an is are be this that its their new study research model models method methods algorithm algorithms learning optimal analysis theoretical theory towards toward how why when what does data neural networks network deep training inference evaluation sample samples general generalized generative efficient robust approach novel our can".split())

def fetch(url,timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":"ICLR27-Research-Baseline/1.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()

def text(s):
    return list(dict.fromkeys(x for x in re.sub("[^a-z0-9 ]"," ",s.lower().replace("-"," ")).split()
                               if len(x)>=3 and x not in STOP))

def save_csv(name, rows, fields):
    with (OUT/name).open("w",encoding="utf-8",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=fields);w.writeheader()
        for r in rows:w.writerow({k:r.get(k) for k in fields})

def main():
    source=json.loads(fetch(BASE+"papers.json",timeout=35))
    site=json.loads(fetch(BASE+"site.json"))
    idx={name:i for i,name in enumerate(source["columns"])}
    papers=[]
    for row in source["rows"]:
        rs=row[idx["ratings"]] or []
        if len(rs)<3:continue
        avg=sum(rs)/len(rs)
        if avg<2.75:continue
        papers.append(dict(number=row[idx["number"]],id=row[idx["forum"]],
                           title=row[idx["title"]],score=round(avg,5),
                           n_reviews=len(rs),ratings=rs))
    papers.sort(key=lambda p:(-p["score"],p["number"]))
    theory=[p for p in papers if THEORY.search(p["title"])]
    save_csv("high_score_all.csv",papers,["number","id","title","score","n_reviews","ratings"])
    save_csv("theory_title_candidates.csv",theory,["number","id","title","score","n_reviews","ratings"])
    # Title similarity is only a PRE-SCREEN, not real bibliographic coupling.
    df=Counter(x for p in theory for x in set(text(p["title"])))
    n=len(theory);idf={x:1+math.log((n+1)/(k+1)) for x,k in df.items()}
    for p in theory:
        toks=text(p["title"]);p["v"]={x:idf[x] for x in toks}
        p["norm"]=math.sqrt(sum(v*v for v in p["v"].values())) or 1
    edges=[]
    for ia,a in enumerate(theory):
        for b in theory[ia+1:]:
            shared=a["v"].keys() & b["v"].keys()
            if len(shared)<2:continue
            sim=sum(a["v"][t]*b["v"][t] for t in shared)/(a["norm"]*b["norm"])
            if sim>=0.23:
                edges.append(dict(id_a=a["id"],id_b=b["id"],title_a=a["title"],
                                  title_b=b["title"],score_a=a["score"],score_b=b["score"],
                                  similarity=round(sim,5),shared_title_terms=";".join(sorted(shared))))
    edges.sort(key=lambda p:(-p["similarity"],-min(p["score_a"],p["score_b"]),p["id_a"]))
    seen=set();pairs=[]
    for p in edges:
        if p["id_a"] in seen or p["id_b"] in seen:continue
        seen.update((p["id_a"],p["id_b"]));pairs.append(p)
        if len(pairs)>=40:break
    save_csv("title_prepairs.csv",pairs,["id_a","id_b","title_a","title_b","score_a","score_b","similarity","shared_title_terms"])
    diagnostics=[]
    # The upstream downloader only understands accepted/rejected; direct OpenReview
    # access for in-review submissions is required. Attempt 16 PDFs + notes.
    try: package=importlib.metadata.version("openreview_downloader")
    except Exception as exc:package=str(exc)
    try:
        import openreview
        c=openreview.api.OpenReviewClient(baseurl="https://api2.openreview.net")
        diagnostics.append({"openreview_client":"initialized"})
    except Exception as exc:
        c=None;diagnostics.append({"openreview_client_error":str(exc)[:400]})
    tried=[]
    for p in pairs[:8]:
        for pid in (p["id_a"],p["id_b"]):
            tried.append(pid)
            rec={"id":pid,"metadata":"not_attempted","pdf":"not_attempted"}
            if c is not None:
                try:
                    note=c.get_note(pid)
                    info=note.to_json() if hasattr(note,"to_json") else {}
                    rec["metadata"]="ok"
                    content=info.get("content") or {}
                    def v(k):
                        x=content.get(k,{})
                        return x.get("value") if isinstance(x,dict) else x
                    rec["primary_area"]=v("primary_area")
                    rec["abstract"]=v("abstract")
                    rec["venueid"]=v("venueid")
                except Exception as e:rec["metadata"]="error: "+str(e)[:280]
            try:
                pdf=fetch("https://openreview.net/pdf?id="+urllib.parse.quote(pid),timeout=12)
                if not pdf.startswith(b"%PDF-"):raise ValueError("Not a PDF response")
                pp=OUT/"pdfs";pp.mkdir(exist_ok=True)
                (pp/(pid+".pdf")).write_bytes(pdf)
                rec["pdf"]="ok"
                rec["pdf_bytes"]=len(pdf);rec["pdf_sha256"]=hashlib.sha256(pdf).hexdigest()
                try:
                    import fitz
                    doc=fitz.open(stream=pdf,filetype="pdf")
                    raw="\n".join(p.get_text("text",sort=True) for p in doc)
                    matches=list(re.finditer(r"(?im)^\\s*(references|bibliography)\\s*$",raw))
                    if matches:
                        snippet=raw[matches[-1].end():][:26000]
                        refs=OUT/"bibliographies";refs.mkdir(exist_ok=True)
                        (refs/(pid+".txt")).write_text(snippet)
                        rec["bibliography_chars"]=len(snippet)
                except Exception as e:rec["text_extraction_error"]=str(e)[:200]
            except Exception as e:rec["pdf"]="error: "+str(e)[:280]
            diagnostics.append(rec)
    with (OUT/"openreview_probe.json").open("w") as fh:json.dump(diagnostics,fh,indent=2,ensure_ascii=False)
    provenance=dict(created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
                    snapshot_id=site.get("latest"),generated_at=site.get("generated_at"),
                    source_url="https://github.com/shiqiangw/iclr-2027-scores",
                    raw_row_count=len(source["rows"]),score275_review3_count=len(papers),
                    title_theory_candidates=len(theory),selected_pairs=len(pairs),
                    openreview_downloader_version=package,pdf_attempt_ids=tried,
                    caution="Title-only heuristic pre-pair; bibliography overlap NOT yet verified.")
    (OUT/"provenance.json").write_text(json.dumps(provenance,indent=2))
    print(json.dumps(provenance,indent=2))
    print("OpenReview PDF success:",sum(x.get("pdf")=="ok" for x in diagnostics))
if __name__=="__main__":main()
