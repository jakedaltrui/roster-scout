suppressPackageStartupMessages({
  library(baseballr)
  library(jsonlite)
  library(rvest)
})

results_dir <- "/spike/results"
year <- 2026L
team_id <- 633
season_team_id <- "615349"
stat_url <- paste0(
  "https://stats.ncaa.org/teams/", season_team_id,
  "/season_to_date_stats?year_stat_category_id=15867"
)

chrome_version <- system2(Sys.getenv("CHROMOTE_CHROME"), "--version", stdout = TRUE)
chrome_major <- sub(".*Chrome ([0-9]+).*", "\\1", chrome_version)
aligned_ua <- paste0(
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ",
  "(KHTML, like Gecko) Chrome/", chrome_major, ".0.0.0 Safari/537.36"
)

namespace <- asNamespace("baseballr")
replace_binding <- function(name, value) {
  unlockBinding(name, namespace)
  assign(name, value, envir = namespace)
  lockBinding(name, namespace)
}

replace_binding(".ncaa_chrome_ua", aligned_ua)
try(baseballr:::.ncaa_browser_close(), silent = TRUE)

started <- Sys.time()
html <- baseballr:::.ncaa_chromote_fetch(stat_url, wait = 45)
duration <- as.numeric(difftime(Sys.time(), started, units = "secs"))
direct <- list(
  duration_seconds = round(duration, 3),
  chrome_version = chrome_version,
  aligned_user_agent = aligned_ua,
  html_bytes = if (is.null(html)) 0L else nchar(html, type = "bytes"),
  blocked = if (is.null(html)) TRUE else baseballr:::.ncaa_is_blocked(html),
  has_stat_grid = if (is.null(html)) FALSE else length(html_elements(read_html(html), "#stat_grid")) > 0
)

resolver <- function(team_id, season_id, ...) {
  lookup <- as.data.frame(baseballr::load_ncaa_baseball_teams())
  hit <- lookup[
    !is.na(lookup$team_id) & !is.na(lookup$season_id) &
      lookup$team_id == team_id & lookup$season_id == season_id,
    "season_team_id",
    drop = TRUE
  ]
  hit <- hit[!is.na(hit)]
  if (length(hit) == 0) NA_character_ else as.character(hit[[1]])
}
replace_binding(".ncaa_resolve_season_team_id", resolver)
try(baseballr:::.ncaa_browser_close(), silent = TRUE)

messages <- character()
started <- Sys.time()
stats <- tryCatch(
  withCallingHandlers(
    baseballr::ncaa_team_player_stats(team_id = team_id, year = year, type = "batting"),
    message = function(m) {
      messages <<- c(messages, conditionMessage(m))
      invokeRestart("muffleMessage")
    }
  ),
  error = function(e) e
)
public_duration <- as.numeric(difftime(Sys.time(), started, units = "secs"))
public <- list(
  duration_seconds = round(public_duration, 3),
  error = if (inherits(stats, "error")) conditionMessage(stats) else NULL,
  rows = if (is.data.frame(stats)) nrow(stats) else 0L,
  columns = if (is.data.frame(stats)) ncol(stats) else 0L,
  column_names = if (is.data.frame(stats)) names(stats) else character(),
  messages = unique(messages)
)

write_json(
  list(
    target = list(team_id = team_id, season_team_id = season_team_id, year = year, division = 3, team_name = "Scranton"),
    direct_aligned_browser = direct,
    public_function_with_id_adapter = public,
    passed = isTRUE(direct$has_stat_grid) && public$rows > 0
  ),
  file.path(results_dir, "compatibility-probe.json"),
  auto_unbox = TRUE, pretty = TRUE, na = "null"
)

try(baseballr:::.ncaa_browser_close(), silent = TRUE)
quit(status = if (isTRUE(direct$has_stat_grid) && public$rows > 0) 0 else 1)
