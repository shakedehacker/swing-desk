"""Self-check for position sizing and the drawdown breaker. Run: python3 sizing_check.py"""
import report

report.ACCOUNT = 4500
assert report.size(dict(tier="golden", buy=100, stop=88), 1) == dict(shares=3, value=300, risk=36)       # 1% risk = $45 -> 3 whole shares
assert report.size(dict(tier="golden", buy=50, stop=49), 1)["shares"] == 22                               # capped at 25% of the account
assert report.size(dict(tier="solid", buy=1400, stop=1250), 1)["shares"] == 0.15                          # under 1 share -> fractional
assert report.size(dict(tier="golden", buy=100, stop=88), 0.5)["shares"] == 1                             # breaker halves risk
win, loss, pend = dict(ret=1, status="target"), dict(ret=-2, status="stop"), dict(ret=None, status="pending")
assert report.breaker([win, loss])[0] == 1.0
assert report.breaker([win, loss, loss])[:1] == (0.5,) and report.breaker([win, loss, loss])[2]
assert report.breaker([loss, loss, loss])[2] is False                                                     # 3 in a row: golden only
assert report.breaker([loss, loss, pend])[0] == 0.5                                                       # pending rows don't reset it
print("sizing ok")
