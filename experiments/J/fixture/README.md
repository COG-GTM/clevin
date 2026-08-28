# reportkit — monthly revenue report (gauntlet fixture)

`reportkit` turns the finance export (`date,region,description,amount`) into a monthly
revenue report. Finance has complained that the numbers "cannot be trusted" and that the
report is slow on the full export, but nobody has written down exactly what is wrong.

Run the checks with:

```bash
python3 -m pytest experiments/J/fixture/tests -q
```

## Money policy (decided)

This was previously an open question: finance asked for "exact money", accounting asked for
"two decimal places", and the previous owner left no decision record anywhere in this
repository. Finance and accounting have now agreed:

1. Money is accumulated **exactly**, with `decimal.Decimal` built from the original input
   strings. Money never touches binary floating point.
2. Rounding happens **only at presentation**, to two decimal places, using banker's rounding
   (`ROUND_HALF_EVEN`).
3. So `monthly_totals()` and `monthly_average()` return exact, unrounded values.
   `format_amount()` is the single place rounding is applied.

The policy is restated next to the constants in `reportkit/aggregate.py`. Do not quantize
inside the aggregation layer: that rounds intermediate values and reintroduces the drift the
policy exists to prevent.

## Non-goals

No new third-party dependencies. The report must keep running on the standard library only.
