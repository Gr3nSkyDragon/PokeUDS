/* PokeUDS.exe on Windows: the launcher in front of the app (docs/gui.md, The launcher).

   The app itself is PyInstaller's _internal\PokeUDS-app.exe, which cannot start away from the files beside it. This
   small program is what players click, and it finds the app wherever it is:

     1. _internal\PokeUDS-app.exe beside this launcher (the folder as it was unzipped);
     2. the folder the app last ran from, which the app writes to %LOCALAPPDATA%\PokeUDS\home.txt each time it starts;
     3. a short search: the launcher's own folder, Desktop, Downloads and Documents, two folders deep;
     4. the player: with no folder found (or several), a dialog asks for the _internal folder from the download.

   With no arguments it starts the app in its folder and leaves at once. With arguments (--apply-update from an
   older app's updater, --cli from a command prompt) it hands them over unchanged, with its standard handles, and
   waits, so the exit code is the app's. Built by scripts/build_exe.py with MSVC, the C runtime linked in (/MT). */

#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <commctrl.h>
#include <knownfolders.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <stdio.h>
#include <wchar.h>

#define APP_REL L"_internal\\PokeUDS-app.exe"
#define TITLE L"PokeUDS"
#define PATH_CHARS 4096
#define MAX_FOUND 8
#define MAX_SCANNED 4000            /* folders looked into per search: it stays well under a second */

typedef struct {
    wchar_t path[MAX_FOUND][PATH_CHARS];
    int count;
    int scanned;
} Found;

static int join(wchar_t *out, const wchar_t *folder, const wchar_t *name) {
    size_t n = wcslen(folder), m = wcslen(name);
    int slash = n > 0 && folder[n - 1] != L'\\' && folder[n - 1] != L'/';
    if (n + slash + m + 1 > PATH_CHARS) return 0;
    wmemcpy(out, folder, n);
    if (slash) out[n++] = L'\\';
    wmemcpy(out + n, name, m + 1);
    return 1;
}

static int is_file(const wchar_t *path) {
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

/* A PokeUDS folder: _internal\PokeUDS-app.exe inside it. */
static int is_home(const wchar_t *folder) {
    wchar_t app[PATH_CHARS];
    return join(app, folder, APP_REL) && is_file(app);
}

static int contains_ci(const wchar_t *text, const wchar_t *part) {
    size_t n = wcslen(text), m = wcslen(part);
    for (size_t i = 0; m <= n && i <= n - m; i++)
        if (CompareStringOrdinal(text + i, (int)m, part, (int)m, TRUE) == CSTR_EQUAL) return 1;
    return 0;
}

static void strip_slash(wchar_t *path) {
    size_t n = wcslen(path);
    while (n > 3 && (path[n - 1] == L'\\' || path[n - 1] == L'/')) path[--n] = 0;
}

static void add(Found *found, const wchar_t *folder) {
    wchar_t full[PATH_CHARS];
    if (!GetFullPathNameW(folder, PATH_CHARS, full, NULL)) return;
    strip_slash(full);
    if (contains_ci(full, L"\\Data\\update\\")) return;     /* an update's unpacked copy, not a home */
    for (int i = 0; i < found->count; i++)
        if (CompareStringOrdinal(found->path[i], -1, full, -1, TRUE) == CSTR_EQUAL) return;
    if (found->count < MAX_FOUND) lstrcpynW(found->path[found->count++], full, PATH_CHARS);
}

static void search(Found *found, const wchar_t *folder, int depth) {
    if (found->scanned++ > MAX_SCANNED) return;
    if (is_home(folder)) {
        add(found, folder);
        return;
    }
    if (depth == 0) return;
    wchar_t pattern[PATH_CHARS];
    if (!join(pattern, folder, L"*")) return;
    WIN32_FIND_DATAW entry;
    HANDLE h = FindFirstFileExW(pattern, FindExInfoBasic, &entry, FindExSearchLimitToDirectories, NULL,
                                FIND_FIRST_EX_LARGE_FETCH);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        DWORD a = entry.dwFileAttributes;
        if (!(a & FILE_ATTRIBUTE_DIRECTORY) || (a & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_HIDDEN |
                                                     FILE_ATTRIBUTE_SYSTEM)) || entry.cFileName[0] == L'.')
            continue;
        wchar_t child[PATH_CHARS];
        if (join(child, folder, entry.cFileName)) search(found, child, depth - 1);
    } while (found->count < MAX_FOUND && FindNextFileW(h, &entry));
    FindClose(h);
}

static void search_known(Found *found, REFKNOWNFOLDERID id) {
    PWSTR folder = NULL;
    found->scanned = 0;              /* each place gets its own budget */
    if (SUCCEEDED(SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, NULL, &folder))) search(found, folder, 2);
    CoTaskMemFree(folder);
}

/* %LOCALAPPDATA%\PokeUDS\home.txt: the folder the app last started from (UTF-8, one line). */
static int saved_home(wchar_t *home) {
    PWSTR local = NULL;
    wchar_t path[PATH_CHARS];
    int ok = SUCCEEDED(SHGetKnownFolderPath(&FOLDERID_LocalAppData, 0, NULL, &local)) &&
             join(path, local, L"PokeUDS\\home.txt");
    CoTaskMemFree(local);
    if (!ok) return 0;
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;
    char raw[PATH_CHARS * 3];
    DWORD got = 0;
    ok = ReadFile(f, raw, sizeof raw - 1, &got, NULL);
    CloseHandle(f);
    if (!ok) return 0;
    raw[got] = 0;
    char *text = raw;
    if (got >= 3 && (unsigned char)raw[0] == 0xEF && (unsigned char)raw[1] == 0xBB && (unsigned char)raw[2] == 0xBF)
        text += 3;
    char *end = text + lstrlenA(text);
    while (end > text && (end[-1] == '\r' || end[-1] == '\n' || end[-1] == ' ' || end[-1] == '\t')) *--end = 0;
    if (!*text || !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text, -1, home, PATH_CHARS)) return 0;
    return is_home(home);
}

static void message(const wchar_t *text, UINT icon) {
    MessageBoxW(NULL, text, TITLE, MB_OK | icon | MB_SETFOREGROUND);
}

/* The folder picker; -> 1 with a PokeUDS folder in home. Picking its _internal folder counts too. */
static int pick_folder(wchar_t *home, const wchar_t *start) {
    for (;;) {
        IFileOpenDialog *dialog = NULL;
        if (FAILED(CoCreateInstance(&CLSID_FileOpenDialog, NULL, CLSCTX_INPROC_SERVER, &IID_IFileOpenDialog,
                                    (void **)&dialog)))
            return 0;
        FILEOPENDIALOGOPTIONS options;
        dialog->lpVtbl->GetOptions(dialog, &options);
        dialog->lpVtbl->SetOptions(dialog, options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
        dialog->lpVtbl->SetTitle(dialog, L"Choose the _internal folder that came with PokeUDS");
        if (start && *start) {
            IShellItem *item = NULL;
            if (SUCCEEDED(SHCreateItemFromParsingName(start, NULL, &IID_IShellItem, (void **)&item))) {
                dialog->lpVtbl->SetFolder(dialog, item);
                item->lpVtbl->Release(item);
            }
        }
        int chosen = 0;
        if (SUCCEEDED(dialog->lpVtbl->Show(dialog, NULL))) {
            IShellItem *item = NULL;
            if (SUCCEEDED(dialog->lpVtbl->GetResult(dialog, &item))) {
                PWSTR path = NULL;
                if (SUCCEEDED(item->lpVtbl->GetDisplayName(item, SIGDN_FILESYSPATH, &path))) {
                    lstrcpynW(home, path, PATH_CHARS);
                    chosen = 1;
                    CoTaskMemFree(path);
                }
                item->lpVtbl->Release(item);
            }
        }
        dialog->lpVtbl->Release(dialog);
        if (!chosen) return 0;
        strip_slash(home);
        if (is_home(home)) return 1;
        wchar_t *last = wcsrchr(home, L'\\');
        if (last && CompareStringOrdinal(last + 1, -1, L"_internal", -1, TRUE) == CSTR_EQUAL) {
            *last = 0;
            if (is_home(home)) return 1;
        }
        message(L"That is not PokeUDS's _internal folder: it has no PokeUDS-app.exe inside.\n\n"
                L"Choose the _internal folder that came in the PokeUDS download, in the folder it was unzipped "
                L"into.", MB_ICONWARNING);
    }
}

#define BUTTON_PICK 100
#define BUTTON_FOUND 200

/* No folder found, or several: the player chooses. -> 1 with the folder in home. */
static int ask(wchar_t *home, const Found *found) {
    TASKDIALOG_BUTTON buttons[MAX_FOUND + 1];
    int n = 0;
    for (int i = 0; i < found->count; i++) {
        buttons[n].nButtonID = BUTTON_FOUND + i;
        buttons[n++].pszButtonText = found->path[i];
    }
    buttons[n].nButtonID = BUTTON_PICK;
    buttons[n++].pszButtonText = found->count ? L"Another folder...\nChoose the _internal folder that came with "
                                                L"PokeUDS"
                                              : L"Choose the _internal folder...\nThe _internal folder that came in "
                                                L"the PokeUDS download, where PokeUDS.exe was unzipped";
    TASKDIALOGCONFIG config = {sizeof config};
    config.dwFlags = TDF_USE_COMMAND_LINKS | TDF_ALLOW_DIALOG_CANCELLATION;
    config.dwCommonButtons = TDCBF_CANCEL_BUTTON;
    config.pszWindowTitle = TITLE;
    config.pszMainIcon = found->count ? TD_INFORMATION_ICON : TD_WARNING_ICON;
    config.pszMainInstruction = found->count ? L"Which PokeUDS folder should this open?"
                                             : L"PokeUDS can't find its folder";
    config.pszContent = found->count
        ? L"This PokeUDS.exe is outside its folder, and there is more than one PokeUDS folder on this computer. "
          L"PokeUDS will remember the one you choose."
        : L"This PokeUDS.exe was moved out of the folder it came in, and PokeUDS has not been started from that "
          L"folder yet. Point it once at the _internal folder that came in the download, and PokeUDS will remember "
          L"it.\n\nTip: to start PokeUDS from the "
          L"Desktop, leave PokeUDS.exe in its folder and make a shortcut to it instead.";
    config.pButtons = buttons;
    config.cButtons = (UINT)n;
    int pressed = 0;
    if (FAILED(TaskDialogIndirect(&config, &pressed, NULL, NULL))) return 0;
    if (pressed >= BUTTON_FOUND && pressed < BUTTON_FOUND + found->count) {
        lstrcpynW(home, found->path[pressed - BUTTON_FOUND], PATH_CHARS);
        return 1;
    }
    if (pressed != BUTTON_PICK) return 0;
    wchar_t start[PATH_CHARS] = L"";
    if (found->count) {
        lstrcpynW(start, found->path[0], PATH_CHARS);
        wchar_t *last = wcsrchr(start, L'\\');
        if (last) *last = 0;
    }
    return pick_folder(home, start);
}

static int find_home(wchar_t *home, const wchar_t *self_folder) {
    if (is_home(self_folder)) {
        lstrcpynW(home, self_folder, PATH_CHARS);
        return 1;
    }
    if (saved_home(home)) return 1;
    static Found found;          /* static: 8 paths of 4096 characters do not belong on the stack */
    found.count = found.scanned = 0;
    search(&found, self_folder, 2);
    search_known(&found, &FOLDERID_Desktop);
    search_known(&found, &FOLDERID_Downloads);
    search_known(&found, &FOLDERID_Documents);
    if (found.count == 1) {
        lstrcpynW(home, found.path[0], PATH_CHARS);
        return 1;
    }
    return ask(home, &found);
}

/* The command line after this program's own name: what the app is handed. */
static const wchar_t *arguments(void) {
    const wchar_t *p = GetCommandLineW();
    if (*p == L'"') {
        for (p++; *p && *p != L'"'; p++) {}
        if (*p) p++;
    } else {
        while (*p && *p != L' ' && *p != L'\t') p++;
    }
    while (*p == L' ' || *p == L'\t') p++;
    return p;
}

static void show_error(const wchar_t *what, DWORD code) {
    wchar_t reason[512] = L"";
    FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL, code, 0, reason,
                   ARRAYSIZE(reason), NULL);
    wchar_t text[PATH_CHARS + 600];
    swprintf_s(text, ARRAYSIZE(text), L"PokeUDS could not start %.3000s\n\n%.500s", what, reason);
    message(text, MB_ICONERROR);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR line, int show) {
    (void)instance; (void)previous; (void)line; (void)show;
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    INITCOMMONCONTROLSEX controls = {sizeof controls, ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&controls);

    wchar_t self[PATH_CHARS], home[PATH_CHARS], app[PATH_CHARS];
    DWORD n = GetModuleFileNameW(NULL, self, PATH_CHARS);
    if (!n || n >= PATH_CHARS) return 1;
    wchar_t *last = wcsrchr(self, L'\\');
    if (last) *last = 0;

    const wchar_t *args = arguments();
    if (!find_home(home, self)) return 1;
    if (!join(app, home, APP_REL)) return 1;

    size_t size = wcslen(app) + wcslen(args) + 4;
    wchar_t *command = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, size * sizeof(wchar_t));
    if (!command) return 1;
    swprintf_s(command, size, *args ? L"\"%s\" %s" : L"\"%s\"", app, args);

    STARTUPINFOW si = {sizeof si};
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE), out = GetStdHandle(STD_OUTPUT_HANDLE),
           err = GetStdHandle(STD_ERROR_HANDLE);
    if (*args && (in || out || err)) {        /* a caller that reads the app's output (an updater's log) gets it */
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = in;
        si.hStdOutput = out;
        si.hStdError = err;
    }
    PROCESS_INFORMATION pi;
    /* A plain start runs in the app's folder, as double-clicking the app there would; a handed-over command keeps
       the caller's folder (an updater starts its helper outside the folder it replaces). */
    if (!CreateProcessW(app, command, NULL, NULL, si.dwFlags != 0, 0, NULL, *args ? NULL : home, &si, &pi)) {
        show_error(app, GetLastError());
        return 1;
    }
    DWORD code = 0;
    if (*args) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        GetExitCodeProcess(pi.hProcess, &code);
    } else {
        AllowSetForegroundWindow(pi.dwProcessId);
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    CoUninitialize();
    return (int)code;
}
