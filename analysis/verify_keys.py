import numpy as np
from scipy.stats import norm, multivariate_normal as mvn
from scipy.optimize import brentq

# ---------- Item GS-01: two-look group-sequential boundaries, IF = 0.5, one-sided alpha = 0.025
rho = np.sqrt(0.5)
S = [[1, rho], [rho, 1]]

# (a) ORIGINAL O'Brien-Fleming: z_k = c*sqrt(K/k)
def alpha_obf(c):
    z1 = c*np.sqrt(2.0); z2 = c
    # P(reject) = P(Z1>=z1) + P(Z1<z1, Z2>=z2)
    p1 = 1 - norm.cdf(z1)
    p2 = mvn(mean=[0,0], cov=S).cdf([z1, np.inf]) - mvn(mean=[0,0], cov=S).cdf([z1, z2])
    return p1 + p2
c = brentq(lambda c: alpha_obf(c) - 0.025, 1.5, 2.5)
print(f"Original OBF: c={c:.4f}  z1={c*np.sqrt(2):.4f}  z2={c:.4f}  nominal p1={1-norm.cdf(c*np.sqrt(2)):.6f}  nominal p2={1-norm.cdf(c):.6f}")

# (b) Lan-DeMets OBF-like spending, gsDesign sfLDOF: a*(t)=2(1-Phi(Phi^{-1}(1-alpha/2)/sqrt(t)))
alpha = 0.025
def sfLDOF(t, alpha=alpha):
    return 2*(1 - norm.cdf(norm.ppf(1-alpha/2)/np.sqrt(t)))
a1 = sfLDOF(0.5); z1 = norm.ppf(1-a1)
print(f"LD-OBF spending: cumulative spend at IF=0.5 = {a1:.6f} -> z1 = {z1:.4f}")
inc = alpha - a1
f = lambda z2: (mvn(mean=[0,0], cov=S).cdf([z1, np.inf]) - mvn(mean=[0,0], cov=S).cdf([z1, z2])) - inc
z2 = brentq(f, 0.5, 4.0)
print(f"                incremental spend = {inc:.6f} -> z2 = {z2:.4f}  (nominal p = {1-norm.cdf(z2):.6f})")

# ---------- Item MT-01: graphical approach (Bretz et al.) weights
# H1,H2 initial weights 0.5/0.5, alpha=0.025; graph: H1->H2 (1), H2->H1 (1), then both -> H3
# Scenario: H1 rejected at alpha1=0.0125 -> weight passes to H2
print("\nGraphical approach: alpha available to H2 after rejecting H1 =", 0.5*0.025 + 0.5*0.025)

# ---------- Item RD-01: rare event risk difference, zero events one arm
# 0/200 vs 6/200 ; Wald RD, Newcombe hybrid-score interval
def wilson(x, n, alpha=0.05):
    z = norm.ppf(1-alpha/2); p = x/n
    d = 1 + z**2/n
    c0 = (p + z**2/(2*n))/d
    hw = z*np.sqrt(p*(1-p)/n + z**2/(4*n**2))/d
    return c0-hw, c0+hw
x1,n1,x2,n2 = 0,200,6,200
l1,u1 = wilson(x1,n1); l2,u2 = wilson(x2,n2)
rd = x2/n2 - x1/n1
lo = rd - np.sqrt((u1-x1/n1)**2 + (x2/n2-l2)**2)
hi = rd + np.sqrt((x1/n1-l1)**2 + (u2-x2/n2)**2)
print(f"RD = {rd:.4f}; Newcombe 95% CI = ({lo:.4f}, {hi:.4f})")
