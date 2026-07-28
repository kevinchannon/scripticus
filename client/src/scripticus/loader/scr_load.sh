# scr_load — source a Scripticus library into the current shell (D57).
#
# Written in POSIX sh on purpose: it is sourced into whatever shell the consumer
# runs, so it must be the most portable thing in the system. No bashisms, no
# `local`, no forks.
#
# Two ways this file reaches a script: a command Scripticus launches through its
# own shim gets it sourced automatically, and a user's own ad-hoc script opts in
# via `scripticus init`, which exports SCRIPTICUS_LIB globally.
#
#     scr_load acme/strings   # then call the functions it defines
#
# Behaviour, all of it deliberate:
#   - references are fully namespaced, and never carry a version (the installed
#     closure pins exactly one, so a version here could only contradict it);
#   - loading is transitive — a library's own load script may call scr_load;
#   - it is idempotent, guarded by a list of what this process has already
#     loaded, so diamonds cost nothing and cycles terminate;
#   - a miss returns non-zero rather than aborting: the caller decides whether a
#     missing library is fatal.

scr_load() {
    if [ $# -ne 1 ] || [ -z "$1" ]; then
        echo "scr_load: usage: scr_load <namespace>/<name>" >&2
        return 2
    fi

    # Already loaded in this process? Nothing to do. Checked before anything
    # else so the common repeat call is as cheap as possible.
    case " ${__scr_loaded-} " in
        *" $1 "*) return 0 ;;
    esac

    # Fully-namespaced references only (v1, D46) — exactly one slash, with
    # something either side of it.
    case "$1" in
        */*/* | /* | */)
            echo "scr_load: '$1' is not a 'namespace/name' reference" >&2
            return 2
            ;;
        */*) ;;
        *)
            echo "scr_load: '$1' is not fully namespaced — write 'namespace/$1'" >&2
            return 2
            ;;
    esac

    if [ ! -f "${SCRIPTICUS_LIB:-$HOME/.scripticus/lib}/$1/load.sh" ]; then
        echo "scr_load: no library '$1' is installed (try: scripticus install $1)" >&2
        return 1
    fi

    # Mark it loaded *before* sourcing, so a cycle between two libraries stops
    # here rather than recursing until the shell gives up.
    __scr_loaded="${__scr_loaded-} $1"

    # A function's positional parameters are per-invocation even in POSIX sh,
    # which makes them the one place a recursive call cannot clobber. Stash the
    # caller's SCR_LIB_DIR there across the source, so a library that loads
    # another library still sees its own directory afterwards.
    set -- "${SCRIPTICUS_LIB:-$HOME/.scripticus/lib}/$1/load.sh" \
        "${SCR_LIB_DIR+set}" "${SCR_LIB_DIR-}"

    . "$1"
    # Captured immediately, before anything else can run and overwrite it.
    __scr_status=$?

    if [ -n "$2" ]; then
        SCR_LIB_DIR="$3"
    else
        unset SCR_LIB_DIR
    fi
    return "$__scr_status"
}
