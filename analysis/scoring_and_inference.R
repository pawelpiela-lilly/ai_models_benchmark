## =============================================================================
## BioStatBench - scoring and inference
##
## Treat this file as the SAP. Nothing here should be run for the first time on
## the real results: run it against simulate_results() first, agree the outputs,
## then freeze it.
##
## Dependencies: base R + stats. lme4 is used only for the GLMM and the G-study
## and is guarded, so the rest of the file runs without it.
##
## STATUS: the numeric routines rg_adjust(), rg_ci(), n_mcnemar() and
## mdd_mcnemar() were validated by Monte Carlo simulation before being written
## here (see validate_rg_coverage() and validate_mcnemar_power() at the bottom).
## The R code itself has NOT been executed - run the validation functions once
## in your own session before relying on it.
## =============================================================================

suppressWarnings(RNGkind("Mersenne-Twister"))


## -----------------------------------------------------------------------------
## 1. Scoring
## -----------------------------------------------------------------------------

#' Severity-weighted score for one response against a rubric
#'
#' @param met named logical vector, criterion_id -> criterion met
#' @param rubric data.frame with criterion_id, kind, severity, weight
#' @return list(score, s1_free, n_s1_violations)
#'
#' Scoring rules:
#'  - `required`      : credit = weight if met
#'  - `alternative`   : credit = weight if ANY criterion in the group is met,
#'                      counted once for the group
#'  - `disqualifying` : if met (i.e. the bad behaviour is present), subtract the
#'                      weight and record a severity violation
score_response <- function(met, rubric) {
  stopifnot(all(c("criterion_id", "kind", "severity", "weight") %in% names(rubric)))
  rubric$met <- as.logical(met[rubric$criterion_id])
  rubric$met[is.na(rubric$met)] <- FALSE

  req <- rubric[rubric$kind == "required", ]
  dis <- rubric[rubric$kind == "disqualifying", ]
  alt <- rubric[rubric$kind == "alternative", ]

  earned <- sum(req$weight[req$met])
  possible <- sum(req$weight)

  if (nrow(alt) > 0) {
    grp <- if ("alternative_group" %in% names(alt)) alt$alternative_group else rep("g", nrow(alt))
    for (g in unique(grp)) {
      sub <- alt[grp == g, ]
      w <- max(sub$weight)
      possible <- possible + w
      if (any(sub$met)) earned <- earned + w
    }
  }

  penalty <- sum(dis$weight[dis$met])
  s1_viol <- sum(dis$met & dis$severity == "S1") +
             sum(!req$met & req$severity == "S1")

  score <- if (possible > 0) max(0, (earned - penalty) / possible) else NA_real_
  list(score = score, s1_free = (s1_viol == 0), n_s1_violations = s1_viol)
}


#' Blueprint-weighted aggregate score across domains
#'
#' Unweighted means over an unbalanced item bank silently reweight the blueprint.
#' Always aggregate within domain first, then weight.
blueprint_score <- function(df, weights) {
  stopifnot(all(c("domain", "score") %in% names(df)))
  dm <- tapply(df$score, df$domain, mean, na.rm = TRUE)
  w <- weights[names(dm)]
  if (any(is.na(w))) stop("domain(s) with no blueprint weight: ",
                          paste(names(dm)[is.na(w)], collapse = ", "))
  sum(dm * w) / sum(w)
}


## -----------------------------------------------------------------------------
## 2. Judge misclassification correction (Rogan-Gladen)
##
## The naive proportion judged correct is a BIASED estimator of the proportion a
## human would judge correct. Direction of bias depends on the true accuracy:
##   E[p_hat] = theta + (2 - q0 - q1) * ((1 - q0)/(2 - q0 - q1) - theta)
## so it overstates low accuracy and understates high accuracy.
##
## Simulated coverage of the naive 95% CI, with q0 = 0.70, q1 = 0.90, n = 1000,
## m0 = m1 = 250, 4000 replicates:
##   theta = 0.30 -> 0.000   |  adjusted 0.948
##   theta = 0.50 -> 0.000   |  adjusted 0.952
##   theta = 0.70 -> 0.690   |  adjusted 0.958
##   theta = 0.85 -> 0.092   |  adjusted 0.951
## Reporting an uncorrected judge proportion is not a conservative simplification.
## It is wrong in an unknown direction.
##
## Refs: Rogan & Gladen (1978) Am J Epidemiol 107:71-76;
##       Lang & Reiczigel (2014) Prev Vet Med 113:13-22;
##       Lee et al. (2025) arXiv:2511.21140.
## -----------------------------------------------------------------------------

#' Bias-adjusted point estimate of true accuracy
#' @param p naive proportion judged correct on the test set
#' @param q0 judge specificity (P(judge says incorrect | truly incorrect))
#' @param q1 judge sensitivity (P(judge says correct   | truly correct))
rg_adjust <- function(p, q0, q1) {
  if (q0 + q1 <= 1) stop("judge is uninformative (q0 + q1 <= 1); correction undefined")
  pmin(1, pmax(0, (p + q0 - 1) / (q0 + q1 - 1)))
}

#' Adjusted (1 - alpha) CI propagating test-set AND calibration-set uncertainty
#' @param n  number of judged test items
#' @param m0 calibration items whose true label is "incorrect"
#' @param m1 calibration items whose true label is "correct"
rg_ci <- function(p, q0, q1, n, m0, m1, alpha = 0.05) {
  z  <- stats::qnorm(1 - alpha / 2)
  pt  <- (n * p + z^2 / 2) / (n + z^2)          # Agresti-Coull style adjustment
  q0t <- (m0 * q0 + 1) / (m0 + 2)
  q1t <- (m1 * q1 + 1) / (m1 + 2)
  nt  <- n + z^2; m0t <- m0 + 2; m1t <- m1 + 2
  th  <- (pt + q0t - 1) / (q0t + q1t - 1)
  dth <- 2 * z^2 * (-(1 - th) * q0t * (1 - q0t) / m0t + th * q1t * (1 - q1t) / m1t)
  se  <- sqrt(pt * (1 - pt) / nt +
              (1 - th)^2 * q0t * (1 - q0t) / m0t +
              th^2       * q1t * (1 - q1t) / m1t) / (q0t + q1t - 1)
  c(lower = max(0, th + dth - z * se),
    est   = min(1, max(0, th + dth)),
    upper = min(1, th + dth + z * se))
}

#' Optimal split of a fixed calibration budget (Lee et al. 2025, Prop. 2)
#'
#' Note the practical implication: once n is large, judging MORE items does not
#' narrow the interval. Only calibration labels do. Budget human time there.
rg_calibration_alloc <- function(m_total, p_hat, q0_pilot, q1_pilot) {
  kappa <- (1 - q0_pilot) / (1 - q1_pilot)
  m1 <- round(m_total / (1 + (1 / p_hat - 1) * sqrt(kappa)))
  m1 <- min(max(m1, 10), m_total - 10)
  c(m0 = m_total - m1, m1 = m1)
}

#' Judge-human agreement, chance-corrected. Gwet's AC1 is preferred to Cohen's
#' kappa here because rubric-criterion prevalence is often extreme, which is
#' exactly where kappa collapses (the "kappa paradox").
gwet_ac1 <- function(rater1, rater2) {
  ok <- !is.na(rater1) & !is.na(rater2)
  r1 <- rater1[ok]; r2 <- rater2[ok]; n <- length(r1)
  pa <- mean(r1 == r2)
  pi_hat <- (mean(r1 == 1) + mean(r2 == 1)) / 2
  pe <- 2 * pi_hat * (1 - pi_hat)
  (pa - pe) / (1 - pe)
}


## -----------------------------------------------------------------------------
## 3. Primary model: GLMM with model fixed, item random
##
## logit P(Y_mir = 1) = alpha_m + b_item + c_domain
##
## This is a Rasch model with FIXED examinee effects. Do not fit 2PL/3PL IRT with
## 8-15 models: item discrimination is not estimable at that examinee count, and
## treating replicates as extra examinees understates uncertainty because they
## are not independent draws from a population of examinees.
## -----------------------------------------------------------------------------

fit_glmm <- function(df) {
  if (!requireNamespace("lme4", quietly = TRUE))
    stop("lme4 required for fit_glmm()")
  df$model <- stats::relevel(factor(df$model), ref = sort(unique(df$model))[1])
  lme4::glmer(correct ~ model + (1 | item_id) + (1 | domain),
              data = df, family = stats::binomial,
              control = lme4::glmerControl(optimizer = "bobyqa"))
}

#' Marginal (population-averaged) accuracy per model, integrating over the
#' random effects rather than evaluating at u = 0.
marginal_accuracy <- function(fit, n_draws = 2000, seed = 1) {
  if (!requireNamespace("lme4", quietly = TRUE)) stop("lme4 required")
  set.seed(seed)
  vc <- as.data.frame(lme4::VarCorr(fit))
  sd_tot <- sqrt(sum(vc$vcov))
  fe <- lme4::fixef(fit)
  lvls <- levels(fit@frame$model)
  eta0 <- fe[["(Intercept)"]]
  u <- stats::rnorm(n_draws, 0, sd_tot)
  vapply(lvls, function(l) {
    add <- if (l == lvls[1]) 0 else fe[[paste0("model", l)]]
    mean(stats::plogis(eta0 + add + u))
  }, numeric(1))
}


## -----------------------------------------------------------------------------
## 4. Paired model comparison
##
## Responses are paired across models on the same items, so use paired inference.
## Independent-sample tests on a paired design discard the pairing and lose power.
## -----------------------------------------------------------------------------

#' All pairwise exact conditional McNemar tests with Holm adjustment
#' @param df long: item_id, model, correct (0/1). Collapse replicates first
#'           (e.g. majority vote or first replicate) or pass replicate-level data
#'           and set collapse = "mean_then_threshold".
pairwise_mcnemar <- function(df, alpha = 0.05) {
  wide <- stats::reshape(df[, c("item_id", "model", "correct")],
                         idvar = "item_id", timevar = "model", direction = "wide")
  cols <- setdiff(names(wide), "item_id")
  models <- sub("^correct\\.", "", cols)
  out <- list(); k <- 0
  for (i in seq_along(cols)) for (j in seq_along(cols)) {
    if (j <= i) next
    a <- wide[[cols[i]]]; b <- wide[[cols[j]]]
    ok <- !is.na(a) & !is.na(b); a <- a[ok]; b <- b[ok]
    n10 <- sum(a == 1 & b == 0); n01 <- sum(a == 0 & b == 1)
    nd <- n10 + n01
    p <- if (nd == 0) 1 else stats::binom.test(n10, nd, 0.5)$p.value
    k <- k + 1
    out[[k]] <- data.frame(
      model_a = models[i], model_b = models[j],
      acc_a = mean(a), acc_b = mean(b),
      diff = mean(a) - mean(b),
      discordant = nd, discordance_rate = nd / length(a),
      p_raw = p, stringsAsFactors = FALSE)
  }
  res <- do.call(rbind, out)
  res$p_holm <- stats::p.adjust(res$p_raw, method = "holm")
  res$significant <- res$p_holm < alpha
  res[order(res$p_holm), ]
}

#' Bradley-Terry ability scores from item-level wins, for an overall ranking
#' with a common scale. Fitted as a logistic regression on paired contrasts.
bt_ranking <- function(df) {
  wide <- stats::reshape(df[, c("item_id", "model", "correct")],
                         idvar = "item_id", timevar = "model", direction = "wide")
  cols <- setdiff(names(wide), "item_id")
  models <- sub("^correct\\.", "", cols)
  rows <- list(); k <- 0
  for (i in seq_along(cols)) for (j in seq_along(cols)) {
    if (j <= i) next
    a <- wide[[cols[i]]]; b <- wide[[cols[j]]]
    ok <- !is.na(a) & !is.na(b)
    wins_a <- sum(a[ok] == 1 & b[ok] == 0)
    wins_b <- sum(a[ok] == 0 & b[ok] == 1)
    x <- matrix(0, nrow = 1, ncol = length(models),
                dimnames = list(NULL, models))
    x[1, models[i]] <- 1; x[1, models[j]] <- -1
    k <- k + 1
    rows[[k]] <- data.frame(wins_a = wins_a, wins_b = wins_b, x, check.names = FALSE)
  }
  d <- do.call(rbind, rows)
  X <- as.matrix(d[, models, drop = FALSE])
  X <- X[, -1, drop = FALSE]  # reference = first model, ability fixed at 0
  fit <- stats::glm(cbind(d$wins_a, d$wins_b) ~ X - 1, family = stats::binomial)
  est <- c(0, stats::coef(fit)); names(est) <- models
  se  <- c(0, summary(fit)$coefficients[, 2]); names(se) <- models
  data.frame(model = models, ability = est, se = se,
             lower = est - 1.96 * se, upper = est + 1.96 * se,
             row.names = NULL)[order(-est), ]
}


## -----------------------------------------------------------------------------
## 5. Precision planning
##
## Paired power depends on the DISCORDANCE RATE psi = pi01 + pi10, not on the
## accuracies. Verified against exact conditional simulation: the asymptotic
## formula runs 2-3 percentage points optimistic relative to the exact test, so
## inflate n by ~5-10% or use exact power.
##
## Minimum detectable difference, 80% power, two-sided alpha 0.05:
##        n=200   n=400   n=800
## psi .10  6.2pp  4.4pp  3.1pp
## psi .20  8.8pp  6.2pp  4.4pp
## psi .30 10.8pp  7.6pp  5.4pp
##
## Read that table before promising a leaderboard. A 400-item bank cannot
## separate models that differ by 3 percentage points.
## -----------------------------------------------------------------------------

n_mcnemar <- function(p01, p10, alpha = 0.05, power = 0.80, inflate = 1.08) {
  psi <- p01 + p10; delta <- p10 - p01
  if (delta == 0) stop("delta = 0")
  za <- stats::qnorm(1 - alpha / 2); zb <- stats::qnorm(power)
  n <- (za * sqrt(psi) + zb * sqrt(psi - delta^2))^2 / delta^2
  ceiling(n * inflate)
}

mdd_mcnemar <- function(n, psi, alpha = 0.05, power = 0.80) {
  za <- stats::qnorm(1 - alpha / 2); zb <- stats::qnorm(power)
  f <- function(d) (za * sqrt(psi) + zb * sqrt(max(psi - d^2, 1e-12)))^2 - n * d^2
  stats::uniroot(f, c(1e-6, sqrt(psi) - 1e-9))$root
}

#' Cluster-robust CI for a mean score when items share a stem (family_id).
#'
#' Simulated: 60 families of 5 items, intra-family correlation 0.40
#'   -> design effect 1 + (m-1)*rho = 2.6
#'   -> naive 95% CI coverage 0.775 vs cluster-robust 0.939
#' Prefer not to author item families at all. If you must, resample families.
cluster_boot_ci <- function(score, family_id, B = 2000, alpha = 0.05, seed = 1) {
  set.seed(seed)
  fams <- unique(family_id)
  stat <- replicate(B, {
    pick <- sample(fams, length(fams), replace = TRUE)
    mean(unlist(lapply(pick, function(f) score[family_id == f])), na.rm = TRUE)
  })
  c(est = mean(score, na.rm = TRUE),
    lower = stats::quantile(stat, alpha / 2, names = FALSE),
    upper = stats::quantile(stat, 1 - alpha / 2, names = FALSE),
    design_effect = {
      m <- mean(table(family_id))
      fm <- tapply(score, family_id, mean, na.rm = TRUE)
      rho <- max(0, (stats::var(fm, na.rm = TRUE) - stats::var(score, na.rm = TRUE) / m) /
                     stats::var(score, na.rm = TRUE))
      1 + (m - 1) * rho
    })
}


## -----------------------------------------------------------------------------
## 6. G-study / D-study (generalisability theory)
##
## The score depends on which items you happened to author, which judges you
## happened to use, and which replicate you happened to draw. Decompose that.
## Report Phi (absolute decisions) rather than G, because the decision is
## "does this model clear a threshold", not "which model ranks higher".
## -----------------------------------------------------------------------------

g_study <- function(df) {
  if (!requireNamespace("lme4", quietly = TRUE)) stop("lme4 required for g_study()")
  fit <- lme4::lmer(score ~ 1 + (1 | model) + (1 | item_id) + (1 | judge) +
                      (1 | model:item_id) + (1 | model:judge),
                    data = df, REML = TRUE,
                    control = lme4::lmerControl(check.nobs.vs.nlev = "ignore",
                                                check.nobs.vs.nRE  = "ignore"))
  vc <- as.data.frame(lme4::VarCorr(fit))
  g <- function(nm) { v <- vc$vcov[vc$grp == nm]; if (length(v)) v else 0 }
  list(fit = fit,
       components = c(model = g("model"), item = g("item_id"), judge = g("judge"),
                      model_x_item = g("model:item_id"),
                      model_x_judge = g("model:judge"), residual = g("Residual")))
}

#' D-study: Phi and G as a function of the number of items and judges
d_study <- function(vc, n_items, n_judges = 1) {
  s_p  <- vc[["model"]]
  s_i  <- vc[["item"]];  s_j  <- vc[["judge"]]
  s_pi <- vc[["model_x_item"]]; s_pj <- vc[["model_x_judge"]]
  s_e  <- vc[["residual"]]
  grid <- expand.grid(n_items = n_items, n_judges = n_judges)
  grid$rel_err <- with(grid, s_pi / n_items + s_pj / n_judges + s_e / (n_items * n_judges))
  grid$abs_err <- with(grid, grid$rel_err + s_i / n_items + s_j / n_judges)
  grid$G   <- s_p / (s_p + grid$rel_err)
  grid$Phi <- s_p / (s_p + grid$abs_err)
  grid
}


## -----------------------------------------------------------------------------
## 7. Contamination diagnostics
## -----------------------------------------------------------------------------

#' Memorisation index: accuracy drop from original to perturbed variants.
#' A model whose score collapses under numeric substitution or entity swap has
#' memorised, not reasoned. Report per model AND per perturbation kind: entity
#' swaps and logical inversions are the most diagnostic.
memorisation_index <- function(df) {
  agg <- stats::aggregate(correct ~ model + perturbation_kind, df, mean)
  base <- agg[agg$perturbation_kind == "original", c("model", "correct")]
  names(base)[2] <- "acc_original"
  out <- merge(agg[agg$perturbation_kind != "original", ], base, by = "model")
  out$delta <- out$acc_original - out$correct
  out[order(out$model, -out$delta), ]
}

#' Temporal partition: performance on items whose sources pre-date vs post-date
#' each model's training cutoff. A gap present for one model and not others is
#' evidence about that model's training data, not about capability.
temporal_gap <- function(df) {
  a <- stats::aggregate(correct ~ model + post_cutoff_source, df, mean)
  w <- stats::reshape(a, idvar = "model", timevar = "post_cutoff_source",
                      direction = "wide")
  names(w) <- c("model", "acc_pre_cutoff", "acc_post_cutoff")
  w$gap <- w$acc_pre_cutoff - w$acc_post_cutoff
  w[order(-w$gap), ]
}


## -----------------------------------------------------------------------------
## 8. Simulation for dry-running the SAP
## -----------------------------------------------------------------------------

simulate_results <- function(n_items = 400, models = c("M_a", "M_b", "M_c", "M_d"),
                             abilities = c(0, 0.25, 0.55, 1.1), k_rep = 5, seed = 42) {
  set.seed(seed)
  domains <- paste0("D", 1:9)
  w <- c(D1 = .12, D2 = .12, D3 = .10, D4 = .10, D5 = .08,
         D6 = .15, D7 = .12, D8 = .15, D9 = .06)
  item <- data.frame(
    item_id = sprintf("IT%03d", seq_len(n_items)),
    domain  = sample(domains, n_items, TRUE, prob = w),
    diff    = stats::rnorm(n_items, 0, 1.2),
    post_cutoff_source = stats::rbinom(n_items, 1, 0.25) == 1,
    stringsAsFactors = FALSE)
  out <- do.call(rbind, lapply(seq_along(models), function(m)
    do.call(rbind, lapply(seq_len(k_rep), function(r) {
      p <- stats::plogis(abilities[m] - item$diff)
      data.frame(item, model = models[m], replicate = r,
                 correct = stats::rbinom(n_items, 1, p),
                 stringsAsFactors = FALSE)
    }))))
  out$score <- out$correct
  out
}

demo <- function() {
  d <- simulate_results()
  d1 <- d[d$replicate == 1, ]
  cat("\n-- marginal accuracy by model (raw) --\n")
  print(round(tapply(d1$correct, d1$model, mean), 3))
  cat("\n-- pairwise exact McNemar, Holm-adjusted --\n")
  print(pairwise_mcnemar(d1), row.names = FALSE)
  cat("\n-- Bradley-Terry ranking --\n")
  print(bt_ranking(d1), row.names = FALSE)
  cat("\n-- minimum detectable difference at observed discordance --\n")
  psi <- mean(pairwise_mcnemar(d1)$discordance_rate)
  for (n in c(200, 400, 800))
    cat(sprintf("  n = %4d, psi = %.2f -> MDD = %.3f\n", n, psi, mdd_mcnemar(n, psi)))
  cat("\n-- judge-corrected accuracy, naive p = 0.72, q0 = 0.80, q1 = 0.92 --\n")
  print(round(rg_ci(p = 0.72, q0 = 0.80, q1 = 0.92, n = 400, m0 = 150, m1 = 150), 4))
  invisible(d)
}


## -----------------------------------------------------------------------------
## 9. Validation - run these once before trusting the port
## -----------------------------------------------------------------------------

validate_rg_coverage <- function(theta = c(.3, .5, .7, .85), q0 = .70, q1 = .90,
                                 n = 1000, m0 = 250, m1 = 250, R = 2000, seed = 1) {
  set.seed(seed)
  t(vapply(theta, function(th) {
    cov_adj <- 0; cov_naive <- 0
    for (i in seq_len(R)) {
      z <- stats::runif(n) < th
      zh <- ifelse(z, stats::runif(n) < q1, stats::runif(n) > q0)
      p <- mean(zh)
      q0h <- stats::rbinom(1, m0, q0) / m0
      q1h <- stats::rbinom(1, m1, q1) / m1
      ci <- rg_ci(p, q0h, q1h, n, m0, m1)
      cov_adj <- cov_adj + (ci["lower"] <= th && th <= ci["upper"])
      se <- sqrt(p * (1 - p) / n)
      cov_naive <- cov_naive + (abs(p - th) < 1.96 * se)
    }
    c(theta = th, coverage_adjusted = cov_adj / R, coverage_naive = cov_naive / R)
  }, numeric(3)))
  ## Expected (matches the Python reference): adjusted ~0.95 at every theta;
  ## naive ~0.00 at theta = 0.30 and 0.50, ~0.69 at 0.70, ~0.09 at 0.85.
}

validate_mcnemar_power <- function(p01 = .10, p10 = .18, R = 5000, seed = 2) {
  set.seed(seed)
  n <- n_mcnemar(p01, p10, inflate = 1)
  pc <- 1 - p01 - p10
  rej <- mean(replicate(R, {
    cnt <- stats::rmultinom(1, n, c(pc / 2, p01, p10, pc / 2))
    b <- cnt[2]; c_ <- cnt[3]
    if (b + c_ == 0) FALSE else stats::binom.test(b, b + c_, .5)$p.value < .05
  }))
  c(n_formula = n, empirical_power = rej)
  ## Expected: n = 341, empirical power ~0.78 (exact test is conservative
  ## relative to the asymptotic formula - hence the default inflate = 1.08).
}

## Uncomment to dry-run:
# demo()
# validate_rg_coverage()
# validate_mcnemar_power()
