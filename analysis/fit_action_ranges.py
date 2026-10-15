import json,sys,collections
import os; sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.chdir(sys.path[0])
import strategy as S
C=collections.defaultdict(collections.Counter)
nh=0
for l in open('hand_archive.jsonl'):
    r=json.loads(l); nh+=1
    board=r.get('boardCards') or []
    hc={s['seatNumber']:s.get('holeCards') for s in r.get('seats',[]) if s.get('holeCards')}
    me={s['seatNumber'] for s in r.get('seats',[]) if 'billy' in (s.get('agentHandle') or '').lower()}
    n={'Flop':3,'Turn':4,'River':5}
    for ev in r.get('recentEvents') or []:
        st=ev.get('street'); sm=ev.get('summary') or {}
        a=sm.get('action'); sn=sm.get('seatNumber')
        if st not in n or a not in('check','call','bet','raise','all-in') or sn in me or not hc.get(sn) or len(board)<n[st]: continue
        try: cat=S._best_hand_rank([S._parse_card(c) for c in hc[sn]+board[:n[st]]])[0]
        except Exception: continue
        b='air' if cat==0 else 'pair' if cat==1 else 'two_pair+'
        C[(st,a)][b]+=1
print('hands',nh)
for k in sorted(C):
    t=sum(C[k].values())
    if t<10: continue
    print(f"{k[0]:6} {k[1]:7} n={t:5}  "+"  ".join(f"{b}={C[k][b]/t:.2f}" for b in('air','pair','two_pair+')))
