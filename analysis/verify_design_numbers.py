import numpy as np
from math import sqrt
from scipy.stats import norm

rng = np.random.default_rng(20260807)

def clip(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))

def rg_point(p, q0, q1):
    return clip((p + q0 - 1) / (q0 + q1 - 1))

def rg_ci(p, q0, q1, n, m0, m1, alpha=0.05):
    z = norm.ppf(1 - alpha/2)
    pt = (n*p + z**2/2)/(n + z**2)
    q0t = (m0*q0 + 1)/(m0 + 2); q1t = (m1*q1 + 1)/(m1 + 2)
    nt = n + z**2; m0t = m0 + 2; m1t = m1 + 2
    th = (pt + q0t - 1)/(q0t + q1t - 1)
    dth = 2*z**2*(-(1-th)*q0t*(1-q0t)/m0t + th*q1t*(1-q1t)/m1t)
    se = sqrt(pt*(1-pt)/nt + (1-th)**2*q0t*(1-q0t)/m0t + th**2*q1t*(1-q1t)/m1t)/(q0t+q1t-1)
    return clip(th + dth - z*se), clip(th + dth + z*se)

# ---- Validate coverage of Rogan-Gladen adjusted CI vs naive
q0_true, q1_true = 0.70, 0.90
n, m0, m1 = 1000, 250, 250
for theta in [0.30, 0.50, 0.70, 0.85]:
    cov_adj = cov_naive = 0; R = 4000
    for _ in range(R):
        z_true = rng.random(n) < theta
        zhat = np.where(z_true, rng.random(n) < q1_true, rng.random(n) > q0_true)
        p_hat = zhat.mean()
        q0_hat = (rng.binomial(m0, q0_true))/m0
        q1_hat = (rng.binomial(m1, q1_true))/m1
        lo, hi = rg_ci(p_hat, q0_hat, q1_hat, n, m0, m1)
        cov_adj += (lo <= theta <= hi)
        se = sqrt(p_hat*(1-p_hat)/n); 
        cov_naive += (p_hat - 1.96*se <= theta <= p_hat + 1.96*se)
    print(f"theta={theta:.2f}  coverage adjusted={cov_adj/R:.3f}  naive={cov_naive/R:.3f}")
import numpy as np
from math import sqrt
from scipy.stats import norm, binom
rng = np.random.default_rng(7)

def n_mcnemar(p01, p10, alpha=0.05, power=0.80):
    psi = p01 + p10; delta = p10 - p01
    za = norm.ppf(1-alpha/2); zb = norm.ppf(power)
    return (za*sqrt(psi) + zb*sqrt(psi - delta**2))**2 / delta**2

def sim_power(n, p01, p10, alpha=0.05, R=20000):
    pc = 1 - p01 - p10
    rej = 0
    for _ in range(R):
        cnt = rng.multinomial(n, [pc*0.5, p01, p10, pc*0.5])  # concordant split arbitrary
        b, c = cnt[1], cnt[2]
        if b + c == 0: continue
        # exact conditional binomial test
        k = min(b, c); pval = 2*binom.cdf(k, b+c, 0.5); pval = min(pval, 1.0)
        rej += (pval < alpha)
    return rej/R

for (p01, p10) in [(0.10, 0.18), (0.05, 0.12), (0.15, 0.22)]:
    n = n_mcnemar(p01, p10)
    print(f"p01={p01} p10={p10} -> n={n:7.1f}   empirical power at n={int(np.ceil(n))}: {sim_power(int(np.ceil(n)), p01, p10):.3f}")

# minimum detectable difference given n items, discordance psi
def mdd(n, psi, alpha=0.05, power=0.80):
    za = norm.ppf(1-alpha/2); zb = norm.ppf(power)
    # solve (za*sqrt(psi)+zb*sqrt(psi-d^2))^2 = n d^2  numerically
    from scipy.optimize import brentq
    f = lambda d: (za*sqrt(psi)+zb*sqrt(max(psi-d**2,1e-12)))**2 - n*d**2
    return brentq(f, 1e-6, sqrt(psi)-1e-9)
for n in [200, 400, 800]:
    for psi in [0.10, 0.20, 0.30]:
        print(f"n={n:4d} psi={psi:.2f} -> MDD={mdd(n,psi):.3f}")

# cluster design effect check: items nested in families
def sim_cluster(n_fam=60, m=5, p=0.7, rho=0.4, R=3000):
    # beta-binomial style intra-family correlation
    a = p*(1-rho)/rho; b = (1-p)*(1-rho)/rho
    naive_cov = clus_cov = 0
    for _ in range(R):
        pf = rng.beta(a, b, n_fam)
        y = (rng.random((n_fam, m)) < pf[:,None]).astype(float)
        phat = y.mean()
        se_naive = sqrt(phat*(1-phat)/(n_fam*m))
        fam_means = y.mean(1)
        se_clus = fam_means.std(ddof=1)/sqrt(n_fam)
        naive_cov += abs(phat-p) < 1.96*se_naive
        clus_cov  += abs(phat-p) < 1.96*se_clus
    return naive_cov/R, clus_cov/R, 1+(m-1)*rho
print("cluster coverage (naive, cluster-robust), design effect:", sim_cluster())
