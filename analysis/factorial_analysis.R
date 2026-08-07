## =============================================================================
## BioStatBench - analysis for the Claude Code (agentic) surface
##
## Companion to scoring_and_inference.R, which holds the scoring, judge
## misclassification correction, paired comparisons and power routines. Source
## that first; this file adds only what the agentic surface requires.
##
## Reads the CSV emitted by harness/cc_runner.py.
##
## STATUS: written but NOT executed (no R in the authoring environment). Run
## demo_agentic() first, agree the output, then freeze.
## =============================================================================

## source("scoring_and_inference.R")


## -----------------------------------------------------------------------------
## 1. Ingest, with the exclusions applied before anything is estimated
## -----------------------------------------------------------------------------

#' Read a cc_runner.py CSV and apply the pre-specified exclusions.
#'
#' Exclusions are applied here, once, and reported. The single most common way an
#' agentic benchmark misleads is by silently averaging over rows that should have
#' been excluded (wrong model) or censored (truncated).
read_cc_results <- function(path, verbose = TRUE) {
  d <- utils::read.csv(path, stringsAsFactors = FALSE)
  need <- c("item_id", "domain", "type", "model", "arm_id", "effort", "replicate",
            "correct", "status", "model_mismatch", "num_turns", "cost_usd",
            "executed_verification", "claimed_without_execution", "result_subtype")
  miss <- setdiff(need, names(d))
  if (length(miss)) stop("missing columns: ", paste(miss, collapse = ", "))

  d$correct <- suppressWarnings(as.integer(d$correct))
  d$model_mismatch <- ifelse(is.na(d$model_mismatch), 0L, as.integer(d$model_mismatch))

  ## Truncation by --max-turns or --max-budget-usd is a censoring event, not a
  ## wrong answer. Flag it; do not decide here what to do with it.
  d$truncated <- as.integer(grepl("max_turns|budget|error_max_turns", d$result_subtype,
                                  ignore.case = TRUE) |
                            grepl("^fail_rc", d$status) & !is.na(d$num_turns))

  excl <- sum(d$model_mismatch == 1L)
  if (verbose) {
    cat(sprintf("rows read              : %d\n", nrow(d)))
    cat(sprintf("model mismatches (excl): %d\n", excl))
    cat(sprintf("truncated (censored)   : %d (%.1f%%)\n",
                sum(d$truncated), 100 * mean(d$truncated)))
    cat(sprintf("deterministically graded: %d\n", sum(!is.na(d$correct))))
    if (excl > 0)
      cat("  NOTE: mismatched rows are excluded, not averaged. Check that",
          "--fallback-model was not passed.\n")
  }
  attr(d, "n_excluded_mismatch") <- excl
  d[d$model_mismatch == 0L, ]
}


#' Two pre-specified handlings of truncated runs. Report both; pre-specify which
#' is primary. Scoring truncation as incorrect penalises arms that explore;
#' dropping it favours arms that give up early. Neither is neutral.
apply_truncation_rule <- function(d, rule = c("censor", "incorrect")) {
  rule <- match.arg(rule)
  if (rule == "incorrect") {
    d$correct[d$truncated == 1L] <- 0L
  } else {
    d$correct[d$truncated == 1L] <- NA_integer_
  }
  attr(d, "truncation_rule") <- rule
  d
}


## -----------------------------------------------------------------------------
## 2. Factorial GLMM for the screening stage
##
## logit P(correct) = model + effort + context_mode + model:effort + (1|item) + (1|domain)
##
## Models and effort are fixed: you have a small fixed set of each and want their
## contrasts, not a variance component. Items are random because they are a sample
## from a universe of possible items - that is the whole basis for generalising
## beyond the bank (Miller 2024, arXiv:2411.00640).
##
## The design is UNBALANCED by construction, because effort levels available
## differ by model. A GLMM handles this; a two-way ANOVA table does not. Do not
## report Type III sums of squares here as if the design were balanced - the
## contrast coding would drive the answer.
## -----------------------------------------------------------------------------

fit_factorial <- function(d, interaction = TRUE) {
  if (!requireNamespace("lme4", quietly = TRUE)) stop("lme4 required")
  d <- d[!is.na(d$correct), ]
  d$model <- factor(d$model); d$effort <- factor(d$effort)
  d$bare <- factor(d$bare)
  ## bare is a fixed effect only if it varies. Every scored run is expected to use
  ## --bare (A.4.3), so in practice this column is often constant - a single-level
  ## factor breaks glmer's contrast coding, not a model that should be fit anyway.
  bare_term <- if (nlevels(d$bare) > 1) " + bare" else ""
  form <- stats::as.formula(if (interaction) {
    paste0("correct ~ model * effort", bare_term, " + (1 | item_id) + (1 | domain)")
  } else {
    paste0("correct ~ model + effort", bare_term, " + (1 | item_id) + (1 | domain)")
  })
  lme4::glmer(form, data = d, family = stats::binomial,
              control = lme4::glmerControl(optimizer = "bobyqa",
                                           optCtrl = list(maxfun = 2e5)))
}

#' Likelihood-ratio test for whether effort matters, and whether it matters
#' differently by model. If the interaction is negligible, Stage 2 can fix effort
#' at one level for all models, which halves the confirmation run.
screen_factors <- function(dat) {
  f_int <- fit_factorial(dat, interaction = TRUE)
  f_add <- fit_factorial(dat, interaction = FALSE)
  ## anova.merMod matches fitted models by the deparsed *name* of each call's `data`
  ## argument, not its content - the local variable here must be called `d`, the
  ## same name fit_factorial() uses internally, or anova() refuses to compare them.
  d <- dat[!is.na(dat$correct), ]
  d$model <- factor(d$model); d$bare <- factor(d$bare)
  if (!requireNamespace("lme4", quietly = TRUE)) stop("lme4 required")
  bare_term <- if (nlevels(d$bare) > 1) " + bare" else ""
  f_noeff <- lme4::glmer(stats::as.formula(paste0("correct ~ model", bare_term,
                                                  " + (1 | item_id) + (1 | domain)")),
                         data = d, family = stats::binomial,
                         control = lme4::glmerControl(optimizer = "bobyqa"))
  list(interaction_test = stats::anova(f_add, f_int),
       effort_main_test = stats::anova(f_noeff, f_add),
       fit_interaction = f_int, fit_additive = f_add)
}


## -----------------------------------------------------------------------------
## 3. Variance decomposition including the agent loop
##
## The agent loop adds variance beyond token sampling: different tool sequences,
## different turn counts, different recovery from an error. If the between-
## replicate component within item x arm is large, then items x replicates
## overstates the independent information and the minimum detectable difference
## is the one for n = items, not n = items x replicates.
## -----------------------------------------------------------------------------

agentic_variance <- function(d) {
  if (!requireNamespace("lme4", quietly = TRUE)) stop("lme4 required")
  d <- d[!is.na(d$correct), ]
  fit <- lme4::lmer(correct ~ 1 + (1 | arm_id) + (1 | item_id) + (1 | arm_id:item_id),
                    data = d, REML = TRUE,
                    control = lme4::lmerControl(check.nobs.vs.nlev = "ignore",
                                                check.nobs.vs.nRE = "ignore"))
  vc <- as.data.frame(lme4::VarCorr(fit))
  g <- function(nm) { v <- vc$vcov[vc$grp == nm]; if (length(v)) v else 0 }
  comp <- c(arm = g("arm_id"), item = g("item_id"),
            arm_x_item = g("arm_id:item_id"), replicate_residual = g("Residual"))
  k <- mean(table(interaction(d$arm_id, d$item_id, drop = TRUE)))
  ## Effective replicate multiplier: how much averaging over k replicates actually
  ## buys, given the arm x item component does not shrink with k.
  eff <- (comp[["arm_x_item"]] + comp[["replicate_residual"]] / k) /
         (comp[["arm_x_item"]] + comp[["replicate_residual"]])
  list(components = comp,
       prop = comp / sum(comp),
       mean_replicates = k,
       variance_retained_after_averaging = eff,
       note = paste("If replicate_residual is a small share of the total, extra",
                    "replicates buy little; spend the budget on items instead."))
}


## -----------------------------------------------------------------------------
## 4. Agentic-conduct endpoints (domain D10)
##
## claimed_without_execution is the headline. On a bare API surface, "I verified
## this runs" is unfalsifiable. Here the transcript either contains an execution
## call or it does not, so the most consequential failure mode for a validated
## workflow becomes directly measurable.
## -----------------------------------------------------------------------------

conduct_summary <- function(d) {
  agg <- function(f, nm) {
    a <- stats::aggregate(f, by = list(arm_id = d$arm_id), FUN = mean, na.rm = TRUE)
    names(a)[2] <- nm; a
  }
  out <- Reduce(function(x, y) merge(x, y, by = "arm_id"),
                list(agg(d$executed_verification, "p_executed_verification"),
                     agg(d$claimed_without_execution, "p_claimed_without_execution"),
                     agg(d$truncated, "p_truncated"),
                     agg(suppressWarnings(as.numeric(d$num_turns)), "mean_turns"),
                     agg(suppressWarnings(as.numeric(d$cost_usd)), "mean_cost_usd")))
  n <- stats::aggregate(rep(1, nrow(d)), by = list(arm_id = d$arm_id), FUN = sum)
  names(n)[2] <- "n_runs"
  out <- merge(out, n, by = "arm_id")
  ## Wilson interval on the critical conduct failure rate.
  z <- stats::qnorm(0.975)
  p <- out$p_claimed_without_execution; nn <- out$n_runs
  den <- 1 + z^2 / nn
  ctr <- (p + z^2 / (2 * nn)) / den
  hw <- z * sqrt(p * (1 - p) / nn + z^2 / (4 * nn^2)) / den
  out$cwe_lower <- pmax(0, ctr - hw); out$cwe_upper <- pmin(1, ctr + hw)
  out[order(out$p_claimed_without_execution), ]
}

#' Critical-error-free rate: the primary endpoint for COU B.
#' A run is S1-free only if it is correct AND did not claim verification it never
#' performed. A correct answer arrived at by asserting unexecuted verification is
#' not a pass in a validated workflow - it is a defect that happened to be right.
s1_free_rate <- function(d) {
  d <- d[!is.na(d$correct), ]
  d$s1_free <- as.integer(d$correct == 1L & d$claimed_without_execution == 0L)
  a <- stats::aggregate(d$s1_free, by = list(arm_id = d$arm_id), FUN = mean)
  names(a)[2] <- "s1_free_rate"
  nn <- stats::aggregate(rep(1, nrow(d)), by = list(arm_id = d$arm_id), FUN = sum)
  a$n <- nn$x
  a$se <- sqrt(a$s1_free_rate * (1 - a$s1_free_rate) / a$n)
  a$lower <- pmax(0, a$s1_free_rate - 1.96 * a$se)
  a$upper <- pmin(1, a$s1_free_rate + 1.96 * a$se)
  a[order(-a$s1_free_rate), ]
}


## -----------------------------------------------------------------------------
## 5. Cost-effectiveness frontier
##
## Cost is a first-class endpoint here, not a footnote: an arm that scores two
## points higher for six times the spend is a different decision from an arm that
## scores two points higher for free. Report the Pareto frontier rather than a
## single ranking, and let the reader apply their own willingness to pay.
## -----------------------------------------------------------------------------

cost_frontier <- function(d) {
  d <- d[!is.na(d$correct), ]
  a <- stats::aggregate(cbind(score = d$correct,
                              cost = suppressWarnings(as.numeric(d$cost_usd)),
                              wall = suppressWarnings(as.numeric(d$wall_s))),
                        by = list(arm_id = d$arm_id), FUN = mean, na.rm = TRUE)
  a <- a[order(a$cost), ]
  a$on_frontier <- FALSE
  best <- -Inf
  for (i in seq_len(nrow(a))) {              # cheapest first; keep strict improvements
    if (a$score[i] > best) { a$on_frontier[i] <- TRUE; best <- a$score[i] }
  }
  ## Incremental cost per additional correct item, along the frontier only.
  f <- a[a$on_frontier, ]
  f$incr_cost_per_extra_correct <- c(NA, diff(f$cost) / diff(f$score))
  list(all_arms = a, frontier = f)
}


## -----------------------------------------------------------------------------
## 6. Surface comparison: how much comes from the harness rather than the model
## -----------------------------------------------------------------------------

#' Paired comparison of the same model on the API surface versus the Claude Code
#' surface, on the items both attempted. The difference is the harness effect and
#' is directly relevant to whether to invest in tooling or in model tier.
surface_gap <- function(d_api, d_cc, model) {
  a <- d_api[d_api$model == model & !is.na(d_api$correct), ]
  c_ <- d_cc[d_cc$model == model & !is.na(d_cc$correct), ]
  ia <- stats::aggregate(a$correct, by = list(item_id = a$item_id), FUN = mean)
  ic <- stats::aggregate(c_$correct, by = list(item_id = c_$item_id), FUN = mean)
  m <- merge(ia, ic, by = "item_id", suffixes = c("_api", "_cc"))
  if (!nrow(m)) stop("no overlapping items")
  ## Majority-vote per item, then exact McNemar on the paired binary outcomes.
  b <- as.integer(m$x_api > 0.5); cc <- as.integer(m$x_cc > 0.5)
  n10 <- sum(cc == 1 & b == 0); n01 <- sum(cc == 0 & b == 1)
  p <- if (n10 + n01 == 0) 1 else stats::binom.test(n10, n10 + n01, 0.5)$p.value
  list(model = model, n_items = nrow(m),
       acc_api = mean(b), acc_claude_code = mean(cc),
       harness_effect = mean(cc) - mean(b),
       discordant = n10 + n01, p_exact_mcnemar = p,
       mdd_at_this_n = mdd_mcnemar(nrow(m), (n10 + n01) / nrow(m)))
}


## -----------------------------------------------------------------------------
## 7. Dry run
## -----------------------------------------------------------------------------

simulate_cc_results <- function(n_items = 80, k_rep = 3, seed = 11) {
  set.seed(seed)
  models <- c("claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5", "claude-fable-5")
  ability <- c(-0.35, 0.30, 0.80, 0.55)
  efforts <- c("low", "medium", "high")
  eff_gain <- c(low = -0.30, medium = 0, high = 0.28)
  base_cost <- c("claude-haiku-4-5-20251001" = 0.012, "claude-sonnet-5" = 0.055,
                 "claude-opus-5" = 0.240, "claude-fable-5" = 0.150)
  domains <- paste0("D", c(1, 3, 4, 5, 7, 8, 10))
  item <- data.frame(item_id = sprintf("IT%03d", seq_len(n_items)),
                     domain = sample(domains, n_items, TRUE),
                     type = sample(c("T1_mcq", "T2_numeric", "T3_code", "T7_agentic"),
                                   n_items, TRUE),
                     diff = stats::rnorm(n_items, 0, 1.1),
                     stringsAsFactors = FALSE)
  rows <- list(); k <- 0
  for (mi in seq_along(models)) for (e in efforts) for (r in seq_len(k_rep)) {
    m <- models[mi]
    lin <- ability[mi] + eff_gain[[e]] - item$diff
    ## arm x item interaction: agent-loop variation that does not average out
    lin <- lin + stats::rnorm(n_items, 0, 0.35)
    cr <- stats::rbinom(n_items, 1, stats::plogis(lin))
    exec <- stats::rbinom(n_items, 1, stats::plogis(0.6 + ability[mi]))
    k <- k + 1
    rows[[k]] <- data.frame(
      item, model = m, arm_id = paste(m, e, "bare", "noweb", "acceptEdits", sep = "|"),
      effort = e, bare = 1L, replicate = r, correct = cr,
      status = "ok", model_mismatch = 0L,
      num_turns = stats::rpois(n_items, 6 + 2 * (e == "high")),
      cost_usd = round(base_cost[[m]] * stats::rgamma(n_items, 4, 4) *
                         (1 + 0.5 * (e == "high")), 4),
      wall_s = round(stats::rgamma(n_items, 5, 0.12) * (1 + 0.4 * (e == "high")), 1),
      executed_verification = exec,
      claimed_without_execution = as.integer(exec == 0 &
                                    stats::rbinom(n_items, 1, 0.35) == 1),
      result_subtype = ifelse(stats::rbinom(n_items, 1, 0.03) == 1,
                              "error_max_turns", "success"),
      stringsAsFactors = FALSE)
  }
  do.call(rbind, rows)
}

demo_agentic <- function() {
  d <- simulate_cc_results()
  tmp <- tempfile(fileext = ".csv"); utils::write.csv(d, tmp, row.names = FALSE)
  d <- read_cc_results(tmp)
  d <- apply_truncation_rule(d, "censor")

  cat("\n-- critical-error-free rate (primary endpoint) --\n")
  print(s1_free_rate(d), row.names = FALSE)

  cat("\n-- agentic conduct --\n")
  print(conduct_summary(d), row.names = FALSE)

  cat("\n-- cost frontier --\n")
  cf <- cost_frontier(d)
  print(cf$frontier, row.names = FALSE)

  cat("\n-- variance decomposition (does another replicate buy anything?) --\n")
  if (requireNamespace("lme4", quietly = TRUE)) {
    av <- agentic_variance(d)
    print(round(av$prop, 3)); print(round(av$variance_retained_after_averaging, 3))
  } else cat("  (lme4 not installed)\n")

  cat("\n-- factor screening --\n")
  if (requireNamespace("lme4", quietly = TRUE)) print(screen_factors(d)$effort_main_test)
  else cat("  (lme4 not installed)\n")

  invisible(d)
}

## Uncomment to dry-run:
# source("scoring_and_inference.R"); demo_agentic()
