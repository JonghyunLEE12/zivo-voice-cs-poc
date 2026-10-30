"""Orientation-independent blind votes and exact two-sided sign tests."""
from collections import defaultdict
import csv
import io
import math


def sign_p(wins, losses):
    n = wins + losses
    return min(1.0, 2 * sum(math.comb(n,k) for k in range(min(wins,losses)+1)) / 2**n) if n else 1.0


def aggregate(votes, *, locale=None, native=None, device=None, labels=None):
    groups = defaultdict(lambda: {'x_wins':0,'same':0,'y_wins':0})
    issues = defaultdict(lambda: {'marked':0,'heard':0})
    count = 0
    label = lambda engine: labels[engine] if labels is not None else engine
    for vote in votes:
        if locale and vote['locale'] != locale or native and vote['rater_native'] != native or device and vote['device'] != device:
            continue
        count += 1
        a,b = vote['a']['engine'],vote['b']['engine']
        x,y = sorted([a,b], key=label)
        group = groups[(vote['locale'],x,y)]
        winner = a if vote['choice']=='A' else b if vote['choice']=='B' else None
        group['same' if winner is None else 'x_wins' if winner==x else 'y_wins'] += 1
        for side in ('a','b'):
            engine = vote[side]['engine']
            issues[engine]['heard'] += 1
            issues[engine]['marked'] += bool(vote['pron_issue_'+side])
    rows = []
    for (lang,x,y),g in sorted(groups.items(),key=lambda entry:(entry[0][0],label(entry[0][1]),label(entry[0][2]))):
        n = sum(g.values())
        decisive = g['x_wins']+g['y_wins']
        rows.append({'locale':lang,'x':label(x),'y':label(y),**g,'n':n,
                     'x_pct':100*g['x_wins']/n,'same_pct':100*g['same']/n,'y_pct':100*g['y_wins']/n,
                     'win_rate_excluding_ties':g['x_wins']/decisive if decisive else None,
                     'sign_p_two_sided':sign_p(g['x_wins'],g['y_wins'])})
    return {'n':count,'pairs':rows,'pronunciation':[{'voice':label(e),**v,'rate':v['marked']/v['heard']}
                                                    for e,v in sorted(issues.items(),key=lambda entry:label(entry[0]))],
            'taiwan_accent_evidence':native=='zh-TW'}


def csv_export(result):
    output = io.StringIO()
    columns = ['locale','x','y','n','x_wins','same','y_wins','x_pct','same_pct','y_pct','win_rate_excluding_ties','sign_p_two_sided']
    writer = csv.DictWriter(output,fieldnames=columns)
    writer.writeheader()
    writer.writerows(result['pairs'])
    return output.getvalue()
