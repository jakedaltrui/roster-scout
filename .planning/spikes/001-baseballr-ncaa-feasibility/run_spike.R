suppressPackageStartupMessages({
  library(baseballr)
  library(jsonlite)
})

results_dir <- "/spike/results"
fixtures_dir <- file.path(results_dir, "fixtures")
dir.create(fixtures_dir, recursive = TRUE, showWarnings = FALSE)
events_path <- file.path(results_dir, "events.jsonl")
if (file.exists(events_path)) file.remove(events_path)

now_iso <- function() format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3Z", tz = "UTC")

append_event <- function(category, operation, status, details = list()) {
  event <- c(list(
    timestamp = now_iso(),
    category = category,
    operation = operation,
    status = status
  ), details)
  cat(toJSON(event, auto_unbox = TRUE, null = "null", na = "null"), "\n",
      file = events_path, append = TRUE)
  invisible(event)
}

capture_call <- function(operation, fn, metadata = list(), fixture = NULL) {
  warnings <- character()
  messages <- character()
  started <- Sys.time()
  value <- NULL
  error <- NULL

  value <- tryCatch(
    withCallingHandlers(
      fn(),
      warning = function(w) {
        warnings <<- c(warnings, conditionMessage(w))
        invokeRestart("muffleWarning")
      },
      message = function(m) {
        messages <<- c(messages, conditionMessage(m))
        invokeRestart("muffleMessage")
      }
    ),
    error = function(e) {
      error <<- conditionMessage(e)
      NULL
    }
  )

  duration <- as.numeric(difftime(Sys.time(), started, units = "secs"))
  is_table <- is.data.frame(value)
  rows <- if (is_table) nrow(value) else NA_integer_
  cols <- if (is_table) ncol(value) else NA_integer_
  status <- if (!is.null(error)) "error" else if (is_table && rows == 0) "empty" else "ok"

  details <- c(metadata, list(
    duration_seconds = round(duration, 3),
    rows = rows,
    columns = cols,
    column_names = if (is_table) names(value) else character(),
    warnings = unique(warnings),
    messages = unique(messages),
    error = error
  ))
  append_event("probe", operation, status, details)

  if (!is.null(fixture) && is_table && rows > 0) {
    write_json(
      list(metadata = details, rows = head(value, 2)),
      file.path(fixtures_dir, paste0(fixture, ".json")),
      dataframe = "rows", auto_unbox = TRUE, pretty = TRUE, na = "null"
    )
  }

  list(status = status, value = value, details = details)
}

requested_year <- suppressWarnings(as.integer(Sys.getenv("SPIKE_YEAR", "2026")))
repeat_count <- max(1L, suppressWarnings(as.integer(Sys.getenv("SPIKE_REPEAT", "1"))))

environment <- list(
  timestamp = now_iso(),
  platform = R.version$platform,
  r_version = R.version.string,
  baseballr_version = as.character(packageVersion("baseballr")),
  chromote_version = as.character(packageVersion("chromote")),
  chrome_path = Sys.getenv("CHROMOTE_CHROME"),
  chrome_version = tryCatch(system2(Sys.getenv("CHROMOTE_CHROME"), "--version", stdout = TRUE), error = conditionMessage),
  requested_year = requested_year,
  repeat_count = repeat_count
)
append_event("environment", "runtime", "ok", environment)

lookup_probe <- capture_call(
  "load_ncaa_baseball_teams",
  function() as.data.frame(baseballr::load_ncaa_baseball_teams()),
  fixture = "team-lookup"
)
lookup <- lookup_probe$value

if (!is.data.frame(lookup) || nrow(lookup) == 0) {
  write_json(list(environment = environment, passed = FALSE, reason = "team lookup unavailable"),
             file.path(results_dir, "summary.json"), auto_unbox = TRUE, pretty = TRUE)
  quit(status = 2)
}

lookup$year <- suppressWarnings(as.integer(lookup$year))
lookup$division <- suppressWarnings(as.integer(lookup$division))
available <- lookup[!is.na(lookup$team_id) & lookup$division %in% 1:3, , drop = FALSE]
years_by_division <- lapply(1:3, function(d) unique(available$year[available$division == d]))
common_years <- Reduce(intersect, years_by_division)
year <- if (requested_year %in% common_years) requested_year else max(common_years, na.rm = TRUE)

coverage <- lapply(1:3, function(d) {
  rows <- available[available$year == year & available$division == d, , drop = FALSE]
  list(division = d, year = year, teams = nrow(rows), unique_team_ids = length(unique(rows$team_id)))
})
append_event("coverage", "lookup_divisions", "ok", list(year = year, divisions = coverage))

preferences <- list(
  `1` = c("Florida St.", "Florida State"),
  `2` = c("Tampa"),
  `3` = c("Salisbury", "Wis.-Whitewater", "Wisconsin-Whitewater")
)

choose_team <- function(division) {
  candidates <- available[available$year == year & available$division == division, , drop = FALSE]
  candidates <- candidates[!duplicated(candidates$team_id), , drop = FALSE]
  preferred <- preferences[[as.character(division)]]
  hit <- candidates[candidates$team_name %in% preferred, , drop = FALSE]
  if (nrow(hit) == 0) hit <- candidates[order(candidates$team_name), , drop = FALSE]
  hit[1, , drop = FALSE]
}

selected <- do.call(rbind, lapply(1:3, choose_team))
selected_summary <- lapply(seq_len(nrow(selected)), function(i) {
  list(
    division = as.integer(selected$division[[i]]),
    year = as.integer(selected$year[[i]]),
    team_id = as.character(selected$team_id[[i]]),
    team_name = as.character(selected$team_name[[i]])
  )
})
append_event("selection", "representative_teams", "ok", list(teams = selected_summary))

probes <- list()
for (i in seq_len(nrow(selected))) {
  team <- selected[i, , drop = FALSE]
  division <- as.integer(team$division[[1]])
  team_id <- as.character(team$team_id[[1]])
  team_name <- as.character(team$team_name[[1]])
  metadata <- list(division = division, year = year, team_id = team_id, team_name = team_name)

  key <- paste0("d", division, "_roster")
  probes[[key]] <- capture_call(
    "ncaa_roster",
    function() baseballr::ncaa_roster(team_id = team_id, year = year),
    metadata,
    key
  )

  for (stat_type in c("batting", "pitching", "fielding")) {
    key <- paste0("d", division, "_", stat_type)
    probes[[key]] <- capture_call(
      paste0("ncaa_team_player_stats_", stat_type),
      function() baseballr::ncaa_team_player_stats(team_id = team_id, year = year, type = stat_type),
      c(metadata, list(stat_type = stat_type)),
      key
    )
  }
}

repeat_probes <- list()
if (repeat_count > 0) {
  team <- selected[1, , drop = FALSE]
  for (attempt in seq_len(repeat_count)) {
    key <- paste0("repeat_d1_batting_", attempt)
    repeat_probes[[key]] <- capture_call(
      "repeat_ncaa_team_player_stats_batting",
      function() baseballr::ncaa_team_player_stats(
        team_id = as.character(team$team_id[[1]]), year = year, type = "batting"
      ),
      list(attempt = attempt, division = 1, year = year,
           team_id = as.character(team$team_id[[1]]), team_name = as.character(team$team_name[[1]]))
    )
  }
}

invalid_probe <- capture_call(
  "invalid_stat_type",
  function() baseballr::ncaa_team_player_stats(
    team_id = as.character(selected$team_id[[1]]), year = year, type = "not-a-stat"
  ),
  list(expected_error = TRUE)
)

probe_summaries <- lapply(probes, function(x) x$details)
statuses <- vapply(probes, function(x) x$status, character(1))
required_pass <- length(statuses) == 12 && all(statuses == "ok")
invalid_pass <- identical(invalid_probe$status, "error")

try(baseballr:::.ncaa_browser_close(), silent = TRUE)

summary <- list(
  environment = environment,
  selected_year = year,
  coverage = coverage,
  selected_teams = selected_summary,
  probes = probe_summaries,
  repeat_probes = lapply(repeat_probes, function(x) x$details),
  invalid_input = invalid_probe$details,
  checks = list(
    all_twelve_required_calls_non_empty = required_pass,
    invalid_stat_type_rejected = invalid_pass,
    json_fixtures_written = length(list.files(fixtures_dir, pattern = "[.]json$")) >= 12
  ),
  passed = required_pass && invalid_pass
)
write_json(summary, file.path(results_dir, "summary.json"),
           dataframe = "rows", auto_unbox = TRUE, pretty = TRUE, na = "null")
append_event("summary", "spike_complete", if (summary$passed) "ok" else "failed",
             list(passed = summary$passed, required_statuses = as.list(statuses)))

quit(status = if (summary$passed) 0 else 1)
