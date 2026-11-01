# Assumptions

## Assumption 1: EUR/USD valuation-date rate

- **What I assumed:** I used the ECB's last published reference rate before the Saturday 31 December 2022 valuation date: 30 December 2022, EUR 1 = USD 1.0666.
- **Why:** The ECB does not publish a reference rate on the Saturday valuation date. CVP used ECB reference rates in Q3, so the immediately preceding business day's rate is consistent and reproducible. Source: [ECB reference rates, 30 December 2022](https://www.ecb.europa.eu/stats/exchange/eurofxref/shared/pdf/2022/12/20221230.pdf).
- **What would change if wrong:** Ember Retail GmbH's USD fair value, investment value, unrealized-gain change, and NAV would change by EUR 13,100,000 multiplied by the FX-rate difference.

## Assumption 2: Materiality threshold

- **What I assumed:** A local-currency fair-value change of 20.00% or more is material.
- **Why:** The brief suggests a 10–25% quarterly range. 20% is a defensible midpoint, and assessing local currency excludes FX movement from company-performance flags.
- **What would change if wrong:** The NAV does not change. The R1 review population may change; for example, Harbor Analytics' 17.83% increase would be flagged under a lower threshold.

## Assumption 3: Unmarked recent investment

- **What I assumed:** Ionmere Materials Ltd is carried at its 15 December purchase cost of USD 14,000,000 until its first valuation.
- **Why:** CVP notes that it was acquired in Q4 and will receive its first mark in Q1 2023. Carrying a recent acquisition at cost is the policy suggested by the brief.
- **What would change if wrong:** Closing investments, unrealized gain, and NAV would change by the difference between an approved Q4 mark and USD 14,000,000.
