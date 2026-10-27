// VISION Folder Setup helper.
//
// This tiny native Windows utility lets the user pick one parent location
// (for example D:\) and creates the two top-level folders used by the build
// tutorial:
//
//   <chosen>\VISION-Source   source/build files only
//   <chosen>\VISION          final installed portable app only
//
// It only creates folders and explanatory text files. It does not download
// or install VISION itself.

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define NOMINMAX

#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>

#include <string>

namespace {

constexpr int kIdPath = 1001;
constexpr int kIdBrowse = 1002;
constexpr int kIdCreate = 1003;
constexpr int kIdOpen = 1004;

HWND g_path_edit = nullptr;
HWND g_status = nullptr;
HWND g_open_button = nullptr;
std::wstring g_last_install_dir;

bool IsDirectory(const std::wstring& path) {
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
}

bool PathExists(const std::wstring& path) {
    return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES;
}

std::wstring TrimTrailingSlash(std::wstring path) {
    while (path.size() > 3 && (path.back() == L'\\' || path.back() == L'/')) {
        path.pop_back();
    }
    return path;
}

std::wstring LeafName(std::wstring path) {
    path = TrimTrailingSlash(path);
    const std::size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? path : path.substr(slash + 1);
}

std::wstring ParentName(std::wstring path) {
    path = TrimTrailingSlash(path);
    const std::size_t slash = path.find_last_of(L"\\/");
    if (slash == std::wstring::npos) {
        return path;
    }
    if (slash == 2 && path.size() > 2 && path[1] == L':') {
        return path.substr(0, 3);
    }
    return path.substr(0, slash);
}

std::wstring JoinPath(std::wstring left, const std::wstring& right) {
    left = TrimTrailingSlash(left);
    if (left.empty()) {
        return right;
    }
    if (left.back() != L'\\' && left.back() != L'/') {
        left += L'\\';
    }
    return left + right;
}

bool EqualIgnoreCase(const std::wstring& left, const std::wstring& right) {
    return CompareStringOrdinal(left.c_str(), -1, right.c_str(), -1, TRUE) == CSTR_EQUAL;
}

std::wstring NormalizeChosenParent(std::wstring path) {
    // If the user accidentally picks D:\VISION or D:\VISION-Source itself,
    // treat D:\ as the intended parent instead of nesting two more folders
    // inside the app folder.
    const std::wstring leaf = LeafName(path);
    if (EqualIgnoreCase(leaf, L"VISION") || EqualIgnoreCase(leaf, L"VISION-Source")) {
        return ParentName(path);
    }
    return TrimTrailingSlash(path);
}

bool EnsureDirectory(const std::wstring& path, DWORD* error_out = nullptr) {
    if (IsDirectory(path)) {
        return true;
    }
    const int result = SHCreateDirectoryExW(nullptr, path.c_str(), nullptr);
    if (result == ERROR_SUCCESS || result == ERROR_FILE_EXISTS || result == ERROR_ALREADY_EXISTS) {
        return IsDirectory(path);
    }
    if (error_out != nullptr) {
        *error_out = static_cast<DWORD>(result);
    }
    return false;
}

bool WriteTextIfMissing(const std::wstring& path, const char* text) {
    if (PathExists(path)) {
        return true;
    }
    HANDLE file = CreateFileW(
        path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    const DWORD length = static_cast<DWORD>(std::char_traits<char>::length(text));
    DWORD written = 0;
    const bool ok = WriteFile(file, text, length, &written, nullptr) != FALSE && written == length;
    CloseHandle(file);
    return ok;
}

void SetStatus(const std::wstring& message) {
    if (g_status != nullptr) {
        SetWindowTextW(g_status, message.c_str());
    }
}

std::wstring DefaultParentFolder() {
    if (IsDirectory(L"D:\\")) {
        return L"D:\\";
    }
    wchar_t buffer[MAX_PATH] = {};
    const DWORD length = GetEnvironmentVariableW(L"USERPROFILE", buffer, MAX_PATH);
    if (length > 0 && length < MAX_PATH) {
        return buffer;
    }
    return L"C:\\";
}

int CALLBACK BrowseCallback(HWND hwnd, UINT message, LPARAM, LPARAM data) {
    if (message == BFFM_INITIALIZED && data != 0) {
        SendMessageW(hwnd, BFFM_SETSELECTIONW, TRUE, data);
    }
    return 0;
}

void BrowseForFolder(HWND owner) {
    wchar_t current[MAX_PATH] = {};
    GetWindowTextW(g_path_edit, current, MAX_PATH);
    const std::wstring initial = current;

    BROWSEINFOW info{};
    info.hwndOwner = owner;
    info.lpszTitle = L"Choose the parent folder. Example: choose D:\\ and VISION will create D:\\VISION-Source and D:\\VISION.";
    info.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    info.lpfn = BrowseCallback;
    info.lParam = reinterpret_cast<LPARAM>(initial.c_str());

    PIDLIST_ABSOLUTE item = SHBrowseForFolderW(&info);
    if (item == nullptr) {
        return;
    }
    wchar_t selected[MAX_PATH] = {};
    if (SHGetPathFromIDListW(item, selected)) {
        SetWindowTextW(g_path_edit, selected);
        SetStatus(L"Folder selected. Click “Create the 2 folders”.");
    }
    CoTaskMemFree(item);
}

void CreateFolders(HWND owner) {
    wchar_t buffer[4096] = {};
    GetWindowTextW(g_path_edit, buffer, static_cast<int>(std::size(buffer)));
    std::wstring chosen = buffer;
    if (chosen.empty()) {
        MessageBoxW(owner, L"Choose a folder first.", L"VISION Folder Setup", MB_OK | MB_ICONWARNING);
        return;
    }

    const std::wstring parent = NormalizeChosenParent(chosen);
    DWORD error = ERROR_SUCCESS;
    if (!EnsureDirectory(parent, &error)) {
        const std::wstring message = L"VISION could not use this folder:\r\n" + parent +
                                     L"\r\n\r\nChoose a folder you can write to, such as D:\\.";
        MessageBoxW(owner, message.c_str(), L"VISION Folder Setup", MB_OK | MB_ICONERROR);
        return;
    }

    const std::wstring source_dir = JoinPath(parent, L"VISION-Source");
    const std::wstring install_dir = JoinPath(parent, L"VISION");
    // Create exactly the two top-level folders. The installer/launcher
    // creates its own internal data, downloads, versions, and workspace
    // folders later; pre-creating those would only blur the distinction this
    // helper is meant to make clear.
    const bool ok = EnsureDirectory(source_dir, &error) && EnsureDirectory(install_dir, &error);
    if (!ok) {
        MessageBoxW(owner, L"VISION could not create all folders. Choose a writable internal drive folder and try again.",
                    L"VISION Folder Setup", MB_OK | MB_ICONERROR);
        return;
    }

    WriteTextIfMissing(
        JoinPath(source_dir, L"README-SOURCE.txt"),
        "VISION-Source\r\n"
        "===============\r\n\r\n"
        "Put the VISION source/build files in this folder.\r\n"
        "Open PowerShell in this folder when building the installer.\r\n"
        "Do not install the finished app into this source folder.\r\n");

    WriteTextIfMissing(
        JoinPath(install_dir, L"README-INSTALL.txt"),
        "VISION\r\n"
        "======\r\n\r\n"
        "This is the final installed VISION folder.\r\n"
        "When the installer asks where to install VISION, choose this folder.\r\n"
        "When using the self-updating launcher layout, VISION.exe stays in this\r\n"
        "folder and individual app versions live under versions\\.\r\n"
        "Do not unzip the source code into this folder.\r\n");

    g_last_install_dir = install_dir;
    EnableWindow(g_open_button, TRUE);
    SetStatus(L"Done. Created VISION-Source and VISION.");

    const std::wstring message =
        L"Done. These folders are ready:\r\n\r\n" + source_dir + L"\r\n" + install_dir +
        L"\r\n\r\nPut source/build files only in VISION-Source.\r\n"
          L"Install the finished app only into VISION.";
    MessageBoxW(owner, message.c_str(), L"VISION folders created", MB_OK | MB_ICONINFORMATION);
}

LRESULT CALLBACK WindowProc(HWND hwnd, UINT message, WPARAM w_param, LPARAM l_param) {
    switch (message) {
        case WM_CREATE: {
            CreateWindowExW(
                0, L"STATIC",
                L"Choose one parent folder. VISION will create two different folders inside it:",
                WS_CHILD | WS_VISIBLE, 22, 18, 520, 22, hwnd, nullptr, nullptr, nullptr);
            CreateWindowExW(
                0, L"STATIC",
                L"VISION-Source  = source/build files\r\nVISION             = final installed app",
                WS_CHILD | WS_VISIBLE, 22, 48, 520, 42, hwnd, nullptr, nullptr, nullptr);
            CreateWindowExW(0, L"STATIC", L"Parent folder:", WS_CHILD | WS_VISIBLE,
                            22, 104, 110, 22, hwnd, nullptr, nullptr, nullptr);
            g_path_edit = CreateWindowExW(
                WS_EX_CLIENTEDGE, L"EDIT", DefaultParentFolder().c_str(),
                WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL, 22, 130, 392, 28,
                hwnd, reinterpret_cast<HMENU>(kIdPath), nullptr, nullptr);
            CreateWindowExW(0, L"BUTTON", L"Choose folder…", WS_CHILD | WS_VISIBLE,
                            426, 129, 116, 30, hwnd, reinterpret_cast<HMENU>(kIdBrowse), nullptr, nullptr);
            CreateWindowExW(0, L"BUTTON", L"Create the 2 folders", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
                            22, 178, 180, 36, hwnd, reinterpret_cast<HMENU>(kIdCreate), nullptr, nullptr);
            g_open_button = CreateWindowExW(
                0, L"BUTTON", L"Open VISION folder", WS_CHILD | WS_VISIBLE | WS_DISABLED,
                216, 178, 164, 36, hwnd, reinterpret_cast<HMENU>(kIdOpen), nullptr, nullptr);
            g_status = CreateWindowExW(
                0, L"STATIC", L"Example: choose D:\\ to create D:\\VISION-Source and D:\\VISION.",
                WS_CHILD | WS_VISIBLE, 22, 232, 520, 36, hwnd, nullptr, nullptr, nullptr);
            CreateWindowExW(
                0, L"STATIC", L"This helper only creates folders. It does not download or install VISION.",
                WS_CHILD | WS_VISIBLE, 22, 274, 520, 22, hwnd, nullptr, nullptr, nullptr);
            return 0;
        }
        case WM_COMMAND: {
            switch (LOWORD(w_param)) {
                case kIdBrowse:
                    BrowseForFolder(hwnd);
                    return 0;
                case kIdCreate:
                    CreateFolders(hwnd);
                    return 0;
                case kIdOpen:
                    if (!g_last_install_dir.empty()) {
                        ShellExecuteW(hwnd, L"open", g_last_install_dir.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
                    }
                    return 0;
                default:
                    break;
            }
            break;
        }
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        default:
            break;
    }
    return DefWindowProcW(hwnd, message, w_param, l_param);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    const HRESULT com_result = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    const bool com_started = SUCCEEDED(com_result);

    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.style = CS_HREDRAW | CS_VREDRAW;
    window_class.lpfnWndProc = WindowProc;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    window_class.lpszClassName = L"VisionFolderSetupWindow";

    if (RegisterClassExW(&window_class) == 0) {
        if (com_started) CoUninitialize();
        return 1;
    }

    HWND window = CreateWindowExW(
        0, window_class.lpszClassName, L"VISION Folder Setup",
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
        CW_USEDEFAULT, CW_USEDEFAULT, 580, 350, nullptr, nullptr, instance, nullptr);
    if (window == nullptr) {
        if (com_started) CoUninitialize();
        return 1;
    }

    ShowWindow(window, show);
    UpdateWindow(window);

    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }

    if (com_started) CoUninitialize();
    return static_cast<int>(message.wParam);
}
