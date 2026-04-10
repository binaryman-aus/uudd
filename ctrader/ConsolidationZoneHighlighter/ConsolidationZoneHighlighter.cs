using System;
using cAlgo.API;
using cAlgo.API.Indicators;

namespace cAlgo.Indicators
{
    [Indicator(IsOverlay = true, AutoRescale = false, AccessRights = AccessRights.None)]
    public class ConsolidationZoneHighlighter : Indicator
    {
        // ── Detection ───────────────────────────────────────────────────────
        [Parameter("Lookback Bars", Group = "Detection", DefaultValue = 20, MinValue = 5, MaxValue = 60)]
        public int LookbackBars { get; set; }

        [Parameter("Short ATR Period", Group = "Detection", DefaultValue = 7, MinValue = 2, MaxValue = 30)]
        public int ShortAtrPeriod { get; set; }

        [Parameter("Long ATR Period", Group = "Detection", DefaultValue = 50, MinValue = 10, MaxValue = 200)]
        public int LongAtrPeriod { get; set; }

        [Parameter("Min Score (1-5)", Group = "Detection", DefaultValue = 3, MinValue = 1, MaxValue = 5)]
        public int MinScore { get; set; }

        // ── Criterion 1 — Volatility Compression ────────────────────────────
        [Parameter("C1 Enable", Group = "C1 ATR Compression", DefaultValue = true)]
        public bool C1Enable { get; set; }

        [Parameter("C1 ATR Ratio <=", Group = "C1 ATR Compression", DefaultValue = 0.65, MinValue = 0.3, MaxValue = 1.0, Step = 0.05)]
        public double AtrRatioThreshold { get; set; }

        // ── Criterion 2 — Price Range Tightness ─────────────────────────────
        [Parameter("C2 Enable", Group = "C2 Range Tightness", DefaultValue = true)]
        public bool C2Enable { get; set; }

        [Parameter("C2 Range/ATR <=", Group = "C2 Range Tightness", DefaultValue = 1.5, MinValue = 0.5, MaxValue = 8.0, Step = 0.25)]
        public double RangeRatioThreshold { get; set; }

        // ── Criterion 3 — Bar Overlap ────────────────────────────────────────
        [Parameter("C3 Enable", Group = "C3 Bar Overlap", DefaultValue = true)]
        public bool C3Enable { get; set; }

        [Parameter("C3 Min Overlap %", Group = "C3 Bar Overlap", DefaultValue = 70, MinValue = 20, MaxValue = 95)]
        public int OverlapPct { get; set; }

        // ── Criterion 4 — Choppiness ─────────────────────────────────────────
        [Parameter("C4 Enable", Group = "C4 Choppiness", DefaultValue = true)]
        public bool C4Enable { get; set; }

        [Parameter("C4 Choppiness >=", Group = "C4 Choppiness", DefaultValue = 0.618, MinValue = 0.40, MaxValue = 0.90, Step = 0.05)]
        public double ChoppinessThreshold { get; set; }

        // ── Criterion 5 — Close Clustering ───────────────────────────────────
        [Parameter("C5 Enable", Group = "C5 Close Clustering", DefaultValue = true)]
        public bool C5Enable { get; set; }

        [Parameter("C5 StdDev/ATR <=", Group = "C5 Close Clustering", DefaultValue = 0.30, MinValue = 0.10, MaxValue = 1.0, Step = 0.05)]
        public double ClusterThreshold { get; set; }

        // ── Visual ──────────────────────────────────────────────────────────
        [Parameter("Opacity (0-255)", Group = "Visual", DefaultValue = 60, MinValue = 10, MaxValue = 220)]
        public int FillOpacity { get; set; }

        [Parameter("Show Debug Label", Group = "Visual", DefaultValue = true)]
        public bool ShowDebug { get; set; }

        // ── State ───────────────────────────────────────────────────────────
        private AverageTrueRange _shortAtr;
        private AverageTrueRange _longAtr;

        protected override void Initialize()
        {
            _shortAtr = Indicators.AverageTrueRange(ShortAtrPeriod, MovingAverageType.Simple);
            _longAtr  = Indicators.AverageTrueRange(LongAtrPeriod,  MovingAverageType.Simple);
        }

        public override void Calculate(int index)
        {
            double longATR  = _longAtr.Result[index];
            double shortATR = _shortAtr.Result[index];

            // ── Debug label — always update on live bar ────────────────────
            if (ShowDebug && IsLastBar)
            {
                // Use previous closed bar for accurate evaluation
                int evalIndex = index - 1;
                bool enough = evalIndex >= LongAtrPeriod + LookbackBars && longATR > 0;
                string dbg;
                if (!enough)
                {
                    dbg = "CZH warming up... bar " + index + " / need " + (LongAtrPeriod + LookbackBars);
                }
                else
                {
                    double la = _longAtr.Result[evalIndex];
                    double sa = _shortAtr.Result[evalIndex];
                    bool c1 = C1Enable && C1(la, sa);
                    bool c2 = C2Enable && C2(evalIndex, la);
                    bool c3 = C3Enable && C3(evalIndex);
                    bool c4 = C4Enable && C4(evalIndex);
                    bool c5 = C5Enable && C5(evalIndex, la);
                    int  sc    = (c1?1:0)+(c2?1:0)+(c3?1:0)+(c4?1:0)+(c5?1:0);
                    int  maxSc = (C1Enable?1:0)+(C2Enable?1:0)+(C3Enable?1:0)+(C4Enable?1:0)+(C5Enable?1:0);
                    dbg = "CZH score: " + sc + "/" + maxSc + "  (need " + MinScore + ")\n"
                        + "C1 ATR ratio " + (sa/la).ToString("F2") + ": " + (C1Enable ? Pass(c1) : "off") + "\n"
                        + "C2 Range/ATR " + (WindowRange(evalIndex)/la).ToString("F2") + ": " + (C2Enable ? Pass(c2) : "off") + "\n"
                        + "C3 Overlap: " + (C3Enable ? Pass(c3) : "off") + "\n"
                        + "C4 Choppiness: " + (C4Enable ? Pass(c4) : "off") + "\n"
                        + "C5 StdDev/ATR: " + (C5Enable ? Pass(c5) : "off");
                }
                Chart.DrawStaticText("CZH_DBG", dbg,
                    VerticalAlignment.Top, HorizontalAlignment.Right,
                    Color.FromArgb(220, 20, 20, 20));
            }

            // ── Per-bar zone marking — closed bars only, after warmup ──────
            if (IsLastBar) return;
            if (index < LongAtrPeriod + LookbackBars) return;
            if (longATR <= 0) return;

            bool c1z = C1Enable && C1(longATR, shortATR);
            bool c2z = C2Enable && C2(index, longATR);
            bool c3z = C3Enable && C3(index);
            bool c4z = C4Enable && C4(index);
            bool c5z = C5Enable && C5(index, longATR);
            int  score = (c1z?1:0)+(c2z?1:0)+(c3z?1:0)+(c4z?1:0)+(c5z?1:0);

            string rectId = "CZH_B" + index;

            if (score >= MinScore)
            {
                // Zone boundaries from the N-bar lookback window (percentile-trimmed)
                double zoneHigh, zoneLow;
                PercentileBounds(index, out zoneHigh, out zoneLow);

                // Mark just this one bar: spans from its open time to the next bar's open time
                Color zoneColor = ScoreColor(score);
                var rect = Chart.DrawRectangle(
                    rectId,
                    Bars.OpenTimes[index - LookbackBars + 1],
                    zoneHigh,
                    Bars.OpenTimes[index + 1],
                    zoneLow,
                    zoneColor);
                if (rect != null)
                    rect.IsFilled = true;
            }
            // No rectangle drawn for bars that don't qualify — previous bars' rectangles are unaffected
        }

        // ── Percentile Bounds ──────────────────────────────────────────────
        // Zone top  = 80th percentile of bar highs (trims top 20% of spike wicks)
        // Zone bottom = 20th percentile of bar lows  (trims bottom 20% of spike wicks)
        private void PercentileBounds(int index, out double zoneHigh, out double zoneLow)
        {
            double[] highs = new double[LookbackBars];
            double[] lows  = new double[LookbackBars];
            for (int i = 0; i < LookbackBars; i++)
            {
                highs[i] = Bars.HighPrices[index - i];
                lows[i]  = Bars.LowPrices[index - i];
            }
            Array.Sort(highs);
            Array.Sort(lows);

            int p80 = Math.Min((int)(LookbackBars * 0.80), LookbackBars - 1);
            int p20 = (int)(LookbackBars * 0.20);

            zoneHigh = highs[p80];
            zoneLow  = lows[p20];
        }

        // ── Five Criteria ──────────────────────────────────────────────────

        private bool C1(double longATR, double shortATR)
        {
            if (longATR <= 0) return false;
            return shortATR / longATR <= AtrRatioThreshold;
        }

        private bool C2(int index, double longATR)
        {
            if (longATR <= 0) return false;
            return WindowRange(index) / longATR <= RangeRatioThreshold;
        }

        private bool C3(int index)
        {
            double minOverlap = OverlapPct / 100.0;
            int qualifying = 0;
            for (int i = 0; i < LookbackBars; i++)
            {
                double hi = Bars.HighPrices[index - i];
                double lo = Bars.LowPrices[index - i];
                int overlapping = 0;
                for (int j = 0; j < LookbackBars; j++)
                {
                    if (i == j) continue;
                    if (lo <= Bars.HighPrices[index - j] && hi >= Bars.LowPrices[index - j])
                        overlapping++;
                }
                if (overlapping >= 0.5 * (LookbackBars - 1))
                    qualifying++;
            }
            return (double)qualifying / LookbackBars >= minOverlap;
        }

        private bool C4(int index)
        {
            double sumTR = 0;
            double wHigh = double.MinValue, wLow = double.MaxValue;
            for (int i = 0; i < LookbackBars; i++)
            {
                int bi = index - i;
                double h = Bars.HighPrices[bi];
                double l = Bars.LowPrices[bi];
                double c = bi > 0 ? Bars.ClosePrices[bi - 1] : Bars.ClosePrices[bi];
                sumTR += Math.Max(h - l, Math.Max(Math.Abs(h - c), Math.Abs(l - c)));
                if (h > wHigh) wHigh = h;
                if (l < wLow)  wLow  = l;
            }
            double range = wHigh - wLow;
            if (range <= 0 || sumTR <= 0) return false;
            double ci = Math.Log10(sumTR / range) / Math.Log10(LookbackBars);
            return ci >= ChoppinessThreshold;
        }

        private bool C5(int index, double longATR)
        {
            if (longATR <= 0) return false;
            double sum = 0;
            for (int i = 0; i < LookbackBars; i++)
                sum += Bars.ClosePrices[index - i];
            double mean = sum / LookbackBars;
            double variance = 0;
            for (int i = 0; i < LookbackBars; i++)
            {
                double d = Bars.ClosePrices[index - i] - mean;
                variance += d * d;
            }
            return Math.Sqrt(variance / LookbackBars) / longATR <= ClusterThreshold;
        }

        // ── Helpers ────────────────────────────────────────────────────────

        private double WindowRange(int index)
        {
            double wHigh = double.MinValue, wLow = double.MaxValue;
            for (int i = 0; i < LookbackBars; i++)
            {
                if (Bars.HighPrices[index - i] > wHigh) wHigh = Bars.HighPrices[index - i];
                if (Bars.LowPrices[index - i]  < wLow)  wLow  = Bars.LowPrices[index - i];
            }
            return wHigh - wLow;
        }

        private Color ScoreColor(int score)
        {
            int a = FillOpacity;
            if (score >= 5) return Color.FromArgb(a, 220,  60,  0);   // red-orange
            if (score >= 4) return Color.FromArgb(a, 255, 120,  0);   // dark orange
            if (score >= 3) return Color.FromArgb(a, 255, 165,  0);   // orange
            return              Color.FromArgb(a, 220, 200,  0);      // yellow
        }

        private static string Pass(bool v) { return v ? "PASS" : "fail"; }
    }
}
