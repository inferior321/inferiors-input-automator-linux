; AutoHotkey v2 Generic Auto Clicker with 15 Basic + 15 Position-Based (First) + 15 Position-Based (Second) + 15 Position-Based (Third) Timers
; Four scrollable tabs: "General Timers", "Position Timers 1", "Position Timers 2", "Position Timers 3"
; All settings can be adjusted in real-time without restarting

#Requires AutoHotkey v2.0

; Force all mouse operations to use screen coordinates
CoordMode "Mouse", "Screen"

; ==================== CONFIGURATION ====================
; Change these constants to adjust timer counts, layout, and scroll behavior.
; Everything else adapts automatically.

; --- Timer counts and loop timing ---
global Timers := {
    numBasic:      15,
    numPosition1:  15,
    numPosition2:  15,
    numPosition3:  15,
    loopInterval:  50,   ; ms between main loop ticks
    sleepCheck:    50     ; ms between isRunning checks during sleeps
}
global TOTAL_TIMERS := Timers.numBasic + Timers.numPosition1 + Timers.numPosition2 + Timers.numPosition3
global NUM_TABS     := 4

; --- Window dimensions and margins ---
global Window := {
    width:         385,
    height:        707,
    marginX:       10,
    marginXInner:  15,
    contentWidth:  365
}

; --- Header area (instructions text) ---
global Header := {
    y:      10,
    height: 30
}

; --- Tab layout ---
; NOTE: The global "Mouse button to use:" selector that used to sit between the
; header and the tabs has been removed (mouse button is now per-timer). The tab
; control and everything below it were shifted up by 33px to reclaim that row.
global TabLayout := {
    x:             10,
    y:             42,
    width:         365,
    height:        500,
    contentTop:    72,
    contentBottom: 532,
    scrollStep:    40,   ; Pixels per scroll wheel notch
    clipPadding:   5,    ; Inset for visibility clipping
    contentInset:  5     ; Extra inset at top of scrollable content
}
global TAB_VISIBLE_HEIGHT := TabLayout.contentBottom - TabLayout.contentTop

; --- Spacing constants for control layout ---
global Layout := {
    sectionHeaderH:  20,
    sectionHeaderX:  20,
    sectionHeaderW:  345,
    editWidth:       95,
    labelWidth:      160,
    contentLeft:     30,
    editLeft:        195
}

; --- General timer spacing ---
global GenSpacing := {
    afterHeader: 28,
    afterKey:    28,
    afterClick:  35,
    afterWait:   49
}

; --- Position timer spacing ---
global PosSpacing := {
    afterHeader: 23,
    afterKey:    23,
    afterClick:  28,
    afterWait:   28,
    afterPos:    32
}

; --- In-tab interval control spacing ---
global IntervalLayout := {
    labelW:   190,
    editW:    85,
    suffixW:  80,
    after:    35   ; vertical space after interval row before first timer
}

; --- Position timer "Get Pos" button ---
global PosBtn := {
    x:       298,
    w:       60,
    h:       24,
    yOffset: -2   ; Offset from position edit Y
}

; --- Mode dropdown layout (inline with section header) ---
global ModeDDL := {
    x:       195,
    w:       72,
    yOffset: -2   ; Offset from header Y
}

; --- Per-timer Mouse Button selector ---
; This dropdown shares the same slot as the Key Name edit box. Exactly one of
; the two is visible at a time, driven by the timer's mode:
;   Click   mode -> Mouse Button dropdown visible, shared label = "Mouse Button:"
;   KeyPress mode -> Key Name edit visible,        shared label = "Key Name:"
; The label at the left is a single shared control whose text flips between the
; two strings below (option A). Default selection is "Left".
global MouseBtn := {
    label:    "Mouse Button:",
    keyLabel: "Key Name:",
    options:  ["Left", "Right", "Middle"]
}

; --- Bottom controls (below tabs) ---
global BottomUI := {
    statusY:     547,
    statusH:     25,
    btnY:        575,
    btnH:        40,
    btnLeftW:    178,
    btnRightX:   193,
    btnRightW:   182,
    footerLine1Y:  625,
    footerLine1bY: 647,
    footerLine2Y:  669
}

; --- Font sizes ---
global Fonts := {
    default: "s9",
    status:  "s10 bold",
    btn:     "s12 bold"
}

; --- Click helper timing ---
global ClickTiming := {
    preDelay:       20,   ; ms before mouse down
    postDelay:      20,   ; ms after position click before next
    cursorReturn1:  20,   ; ms after SetCursorPos on return
    cursorReturn2:  20    ; ms after MouseMove on return
}

; --- Tooltip display duration ---
global TOOLTIP_TIMEOUT := 2000

; --- Default values ---
global Defaults := {
    seconds:  "0",
    clickMs:  "0",
    waitMs:   "0",
    position: "0,0",
    key:      ""
}

; --- Win32 cursor constants ---
global CursorConst := {
    idcCross:    32515,   ; Crosshair cursor resource ID
    ocrNormal:   32512,   ; Standard arrow cursor ID for SetSystemCursor
    spiSet:      0x0057   ; SystemParametersInfo action to restore default cursors
}

; --- Win32 hook and message constants ---
global Win32 := {
    whMouseLL:      14,       ; Low-level mouse hook type
    wmMouseWheel:   0x020A,   ; Mouse wheel message ID
    wmSetRedraw:    0x000B,   ; WM_SETREDRAW message ID (wParam 0 = off, 1 = on)
    rdwFlags:       0x0085    ; RedrawWindow flags (RDW_FRAME | RDW_INVALIDATE | RDW_ALLCHILDREN)
}

; ==================== GROUPED STATE ====================

; Runtime state: running flag, position run timestamps.
; (The global click-button dropdown was removed; each timer now carries its own
; Mouse Button selection in its cache.)
global AppState := {
    isRunning: false,
    lastPositionRun: [0, 0, 0]
}

; Scroll/UI state: per-tab scroll offsets, control lists, positions, content heights
global ScrollState := {
    offset: Map(),
    tabControls: Map(),
    controlOrigY: Map(),
    controlHeight: Map(),
    totalContentHeight: Map(),
    ; Per-tab last-applied scroll offset. When offset hasn't changed (e.g. mode
    ; toggles, tab switches that re-apply clipping), skip the per-control Move.
    ; Initialized to a sentinel that won't match any real offset.
    lastAppliedOffset: Map(),
    ; Per-control last visibility state. Toggling Visible is a Win32 round-trip,
    ; so we only call it when the value actually changes.
    lastVisible: Map()
}

; Initialize per-tab scroll state
Loop NUM_TABS {
    ScrollState.offset[A_Index]             := 0
    ScrollState.tabControls[A_Index]        := []
    ScrollState.totalContentHeight[A_Index] := 0
    ScrollState.lastAppliedOffset[A_Index]  := -1   ; sentinel: forces first apply
}

; Per-timer objects: each holds its own clickEdit, waitEdit, modeDDL, buttonDDL, keyEdit, sharedLabel
; General timers stored in generalTimers[], position timers in positionSets[1..3].timers[]
global generalTimers := []

; Factory function for creating a position set with empty defaults
CreatePositionSet() {
    return {timers: [], secondsEdit: "", posEdits: [], posButtons: [], cachedIntervalMs: 0}
}

; Each position set: {timers: [], secondsEdit: "", posEdits: [], posButtons: []}
global positionSets := [CreatePositionSet(), CreatePositionSet(), CreatePositionSet()]

; Custom message ID for scroll routing from low-level hook
global WM_APP_SCROLL       := 0x8001

; ==================== RELEASE ALL KEYS ====================
; Safety function to release all potentially held modifier keys and mouse buttons.
; Called on stop, exit, and GUI close to prevent any keys from getting "stuck".

ReleaseAllKeys() {
    Send("{LControl up}{RControl up}{LShift up}{RShift up}{LAlt up}{RAlt up}{LWin up}{RWin up}")
    Click("Up Left")
    Click("Up Right")
    Click("Up Middle")
}

; ==================== ONEXIT HOOK CLEANUP ====================
; Ensures held keys are released and the low-level mouse hook is removed
; even on unexpected exits or crashes.
OnExit((*) => (ReleaseAllKeys(), RemoveMouseHook()))

; ==================== INTERRUPTIBLE SLEEP ====================
; Replaces blocking Sleep() calls so that stopping the script is near-instant.
; Returns true if the full duration elapsed, false if interrupted by stop.
; FIX #2: Uses A_TickCount for real wall-clock elapsed time instead of
; accumulating requested sleep durations, which drift due to OS scheduling.

InterruptibleSleep(duration) {
    global AppState, Timers
    start := A_TickCount
    while ((A_TickCount - start) < duration && AppState.isRunning) {
        remaining := duration - (A_TickCount - start)
        chunk := Min(Timers.sleepCheck, remaining)
        Sleep(chunk)
    }
    return AppState.isRunning
}

; ==================== QUOTED LITERAL DETECTION ====================
; True if s is a double-quote-wrapped literal: starts and ends with " and
; is at least 2 chars long. A lone " (length 1) is NOT a literal.
IsQuotedLiteral(s) {
    return (StrLen(s) >= 2 && SubStr(s, 1, 1) = '"' && SubStr(s, -1) = '"')
}

; ==================== KEY VALIDATION ====================
; Returns true if keyName is a valid AHK key name or a valid combo (e.g. "LControl+V").
; Supports "+" separated combos with optional spaces around "+".

IsValidKey(keyName) {
    if (keyName = "")
        return false

    ; Double-quote-wrapped literal (e.g. "sleeps", "*sleeps*") is always valid;
    ; its contents are typed verbatim by SendKeyPress. Only the outer quotes are
    ; stripped, so anything between them is accepted as-is.
    if (IsQuotedLiteral(keyName))
        return true

    ; Special case: literal "+" key by itself
    if (Trim(keyName) = "+")
        return true

    ; Support "+" separated combos (e.g. "LControl+V", "Ctrl + Shift + Escape")
    keys := StrSplit(keyName, "+")
    for key in keys {
        key := Trim(key)
        if (key = "")
            return false
        try {
            vk := GetKeyVK(key)
            if (vk = 0)
                return false
        } catch {
            return false
        }
    }
    return true
}

; ==================== TIMER CACHE REFRESH ====================
; Reads all editable values for a single timer in one shot and stores them
; on the timerObj so the runtime hot path doesn't need to issue Win32
; round-trips for every field on every iteration. Called:
;   - Once at timer creation to initialize caches.
;   - From a single shared Change handler whenever the user edits any
;     control belonging to this timer (real-time edit support).
;   - From ResetAll after programmatic value resets (Change does not fire
;     for programmatic Edit.Value writes / DDL.Choose in AHK v2).
;   - From GetPosition after a programmatic position write.
;   - From OnModeChange after a programmatic mode/button/key change.

RefreshTimerCache(timerObj) {
    ; Read each control once, parse to the type the runtime needs.
    ; Number-restricted Edits can be empty (user cleared the field), so
    ; coerce empty to 0 to match the runtime's previous tolerance for
    ; missing values without throwing.
    clickVal := timerObj.clickEdit.Value
    waitVal  := timerObj.waitEdit.Value
    timerObj.cachedClickMs  := (clickVal = "") ? 0 : Integer(clickVal)
    timerObj.cachedWaitMs   := (waitVal  = "") ? 0 : Integer(waitVal)
    timerObj.cachedKey      := Trim(timerObj.keyEdit.Value)
    timerObj.cachedKeyValid := IsValidKey(timerObj.cachedKey)
    ; Snapshot the per-timer Mouse Button selection so the runtime hot path
    ; (ClickAtPosition) can read it without a Win32 round-trip.
    if (timerObj.buttonDDL != "")
        timerObj.cachedClickButton := timerObj.buttonDDL.Text
    if (HasProp(timerObj, "posEdit") && timerObj.posEdit != "")
        timerObj.cachedPosition := timerObj.posEdit.Value
}

; Single shared Change handler for any Edit/DropDownList control belonging to a timer.
; Bound to the owning timerObj when the control is created.
OnTimerControlChange(timerObj, *) {
    RefreshTimerCache(timerObj)
}


; Validates a single timer's key field. Returns true if valid (or not in KeyPress mode),
; false if invalid (shows tooltip).

ValidateTimerKey(t) {
    if (t.currentMode != "KeyPress")
        return true
    keyName := t.cachedKey
    if (keyName != "" && !t.cachedKeyValid) {
        ToolTip("Invalid key name: '" keyName "'`nExamples: a, Space, F1, LControl+V, Ctrl+Shift+Escape")
        SetTimer(() => ToolTip(), -3000)
        return false
    }
    return true
}

; Validates all KeyPress timers across general and position timer arrays.
; Returns true if all are valid, false if any invalid key is found.

ValidateAllKeyTimers() {
    global generalTimers, positionSets

    ; Check general timers
    for t in generalTimers {
        if (!ValidateTimerKey(t))
            return false
    }

    ; Check position timers
    Loop 3 {
        for t in positionSets[A_Index].timers {
            if (!ValidateTimerKey(t))
                return false
        }
    }

    return true
}

; ==================== MODE CHANGE HANDLER ====================
; Flips the shared label text and swaps which control occupies the shared slot
; (Mouse Button dropdown vs. Key Name edit) when the mode dropdown changes.
; timerObj is the per-timer object containing modeDDL, buttonDDL, keyEdit, sharedLabel, posEdit, posLabel, posButton.

OnModeChange(timerObj, ctrlObj, *) {
    global tabs, MouseBtn, Defaults
    ; Cache the current mode on the timer object so runtime loops and
    ; visibility checks don't need to re-read modeDDL.Text via Win32.
    timerObj.currentMode := ctrlObj.Text

    if (ctrlObj.Text = "KeyPress") {
        ; Entering KeyPress: shared label becomes "Key Name:", the Key edit
        ; takes over the slot (handled by RepositionControls via IsHiddenByMode).
        ; Option B: reset the Mouse Button back to Left on leaving Click mode,
        ; mirroring how the typed key is cleared on leaving KeyPress. Programmatic
        ; Choose() does not fire Change in AHK v2, so the cache is refreshed below.
        timerObj.sharedLabel.Text := MouseBtn.keyLabel
        timerObj.buttonDDL.Choose(1)   ; Reset to Left
    } else {
        ; Leaving KeyPress (into Click): shared label becomes "Mouse Button:" and
        ; the typed key is cleared (existing behavior, preserved). Programmatic
        ; writes do not fire Change in AHK v2, so the cache is refreshed below.
        timerObj.sharedLabel.Text := MouseBtn.label
        timerObj.keyEdit.Value := Defaults.key
    }

    ; Refresh the timer's value cache to reflect the programmatic changes above
    ; and to recompute cachedKeyValid / cachedClickButton for the new mode.
    RefreshTimerCache(timerObj)
    ; Re-apply clipping so visibility respects scroll position and the new mode.
    ; RepositionControls handles all show/hide based on currentMode + scroll
    ; state, so direct Visible writes here would be redundant and could desync
    ; the lastVisible cache.
    ApplyScrollClipping(tabs.Value)
}

; ==================== LOW-LEVEL MOUSE HOOK ====================
; WH_MOUSE_LL intercepts mouse messages at the OS level BEFORE they are
; dispatched to any window. If the mouse is over our tab area when a scroll
; happens, we eat the message completely and post a custom message to trigger
; our scroll logic from the AHK thread.

global hMouseHook          := 0
global pMouseHookCallback  := 0

LowLevelMouseProc(nCode, wParam, lParam) {
    global hMouseHook, MyGui, WM_APP_SCROLL, Win32

    if (nCode < 0)
        return DllCall("CallNextHookEx", "Ptr", hMouseHook, "Int", nCode, "UPtr", wParam, "Ptr", lParam, "Ptr")

    if (wParam = Win32.wmMouseWheel) {
        ptX       := NumGet(lParam, 0, "Int")
        ptY       := NumGet(lParam, 4, "Int")
        mouseData := NumGet(lParam, 8, "UInt")

        guiHwnd := MyGui.Hwnd

        ; Convert screen coords to client coords
        pt := Buffer(8, 0)
        NumPut("Int", ptX, pt, 0)
        NumPut("Int", ptY, pt, 4)
        DllCall("ScreenToClient", "Ptr", guiHwnd, "Ptr", pt)
        clientX := NumGet(pt, 0, "Int")
        clientY := NumGet(pt, 4, "Int")

        ; Verify mouse is actually within our window
        winRect := Buffer(16, 0)
        DllCall("GetWindowRect", "Ptr", guiHwnd, "Ptr", winRect)
        winLeft   := NumGet(winRect, 0, "Int")
        winTop    := NumGet(winRect, 4, "Int")
        winRight  := NumGet(winRect, 8, "Int")
        winBottom := NumGet(winRect, 12, "Int")

        if (ptX < winLeft || ptX > winRight || ptY < winTop || ptY > winBottom)
            return DllCall("CallNextHookEx", "Ptr", hMouseHook, "Int", nCode, "UPtr", wParam, "Ptr", lParam, "Ptr")

        ; Check if mouse is within tab area bounds (client coords)
        if (clientY >= TabLayout.y && clientY <= (TabLayout.y + TabLayout.height) && clientX >= TabLayout.x && clientX <= (TabLayout.x + TabLayout.width)) {
            DllCall("PostMessage", "Ptr", guiHwnd, "UInt", WM_APP_SCROLL, "UPtr", mouseData, "Ptr", 0)
            return 1  ; Block this scroll event
        }
    }

    return DllCall("CallNextHookEx", "Ptr", hMouseHook, "Int", nCode, "UPtr", wParam, "Ptr", lParam, "Ptr")
}

InstallMouseHook() {
    global hMouseHook, pMouseHookCallback, Win32
    pMouseHookCallback := CallbackCreate(LowLevelMouseProc, , 3)
    hMouseHook := DllCall("SetWindowsHookEx", "Int", Win32.whMouseLL, "Ptr", pMouseHookCallback, "Ptr", 0, "UInt", 0, "Ptr")
}

RemoveMouseHook() {
    global hMouseHook, pMouseHookCallback
    if (hMouseHook) {
        DllCall("UnhookWindowsHookEx", "Ptr", hMouseHook)
        hMouseHook := 0
    }
    if (pMouseHookCallback) {
        CallbackFree(pMouseHookCallback)
        pMouseHookCallback := 0
    }
}

; ==================== CONTROL REGISTRATION HELPERS ====================
; These reduce repetition when building timer rows.

RegisterControl(tabIndex, ctrl, y) {
    global ScrollState
    ScrollState.tabControls[tabIndex].Push(ctrl)
    ScrollState.controlOrigY[ctrl.Hwnd] := y
}

AddLabel(tabIndex, gui, x, y, w, text, options := "") {
    ctrl := gui.Add("Text", "x" x " y" y " w" w " " options, text)
    RegisterControl(tabIndex, ctrl, y)
    return ctrl
}

AddEditWithUpDown(tabIndex, gui, x, y, w, defaultVal) {
    editCtrl := gui.Add("Edit", "x" x " y" y " w" w " Number", String(defaultVal))
    RegisterControl(tabIndex, editCtrl, y)
    return editCtrl
}

; ==================== HELPER: ADD MODE DROPDOWN + SHARED SLOT ====================
; Adds the Click/KeyPress dropdown inline with the header, a single shared label
; (always visible; text flips between "Mouse Button:" and "Key Name:"), and the
; two controls that share one slot: a Mouse Button dropdown (Click mode) and a
; Key Name edit (KeyPress mode). Exactly one of those two is visible at a time.
; timerObj is the per-timer object that receives modeDDL, buttonDDL, keyEdit,
; sharedLabel references. Returns the key edit control.

AddModeAndKeyControls(tabIndex, gui, headerY, keyRowY, timerObj) {
    global ModeDDL, Layout, Defaults, MouseBtn

    ; Mode dropdown (inline with section header)
    ddl := gui.Add("DropDownList", "x" ModeDDL.x " y" (headerY + ModeDDL.yOffset) " w" ModeDDL.w, ["Click", "KeyPress"])
    ddl.Choose(1)
    timerObj.modeDDL := ddl
    RegisterControl(tabIndex, ddl, headerY + ModeDDL.yOffset)
    ddl.OnEvent("Change", OnModeChange.Bind(timerObj))

    ; Shared label — ALWAYS visible (subject only to scroll clipping). Its text
    ; flips with the mode. Default mode is Click, so it starts as "Mouse Button:".
    ; No isHiddenByMode stamp, so RepositionControls only hides it when scrolled
    ; out of view, never because of the mode.
    sharedLabel := gui.Add("Text", "x" Layout.contentLeft " y" keyRowY " w" Layout.labelWidth, MouseBtn.label)
    timerObj.sharedLabel := sharedLabel
    RegisterControl(tabIndex, sharedLabel, keyRowY)

    ; Mouse Button dropdown — occupies the shared slot in Click mode (default
    ; visible). Same x/y/w as the Key edit so they line up with the other edit
    ; boxes on both edges. Hidden when the timer is in KeyPress mode.
    buttonDDL := gui.Add("DropDownList", "x" Layout.editLeft " y" keyRowY " w" Layout.editWidth, MouseBtn.options)
    buttonDDL.Choose(1)  ; Default to Left
    timerObj.buttonDDL := buttonDDL
    RegisterControl(tabIndex, buttonDDL, keyRowY)
    ; Stamp for O(1) mode-visibility lookup: hidden when mode is KeyPress.
    buttonDDL.isButtonDDL := true
    buttonDDL.timerObj := timerObj
    ; Keep the cached button value in sync on user edits.
    buttonDDL.OnEvent("Change", OnTimerControlChange.Bind(timerObj))

    ; Key edit — occupies the shared slot in KeyPress mode. Hidden by default
    ; (Click mode), shown when KeyPress selected.
    keyEdit := gui.Add("Edit", "x" Layout.editLeft " y" keyRowY " w" Layout.editWidth " Hidden", Defaults.key)
    timerObj.keyEdit := keyEdit
    RegisterControl(tabIndex, keyEdit, keyRowY)
    ; Stamp for O(1) mode-visibility lookup: hidden when mode is Click.
    keyEdit.isKeyEdit := true
    keyEdit.timerObj := timerObj
    ; Update cached key value on user edits so the runtime hot path can skip
    ; Win32 reads. Programmatic writes (ResetAll, etc.) refresh via RefreshTimerCache.
    keyEdit.OnEvent("Change", OnTimerControlChange.Bind(timerObj))

    return keyEdit
}

; ==================== HELPER: ADD INTERVAL CONTROL ====================
; Creates the interval control row for position timer tabs
; Returns the seconds edit control

AddIntervalControl(tabIndex, gui, contentY) {
    global Layout, Defaults

    intervalLabel := gui.Add("Text", "x" Layout.sectionHeaderX " y" contentY " w180 cRed", "Execution Interval (ms):")
    intervalLabel.SetFont("Bold")
    RegisterControl(tabIndex, intervalLabel, contentY)

    secondsEdit := gui.Add("Edit", "x" Layout.editLeft " y" (contentY - 3) " w" Layout.editWidth " Number", Defaults.seconds)
    RegisterControl(tabIndex, secondsEdit, contentY - 3)

    return secondsEdit
}

; ==================== HELPER: CREATE TIMER ROWS ====================
; Shared logic for creating timer rows (both general and position).
; timerType: "General" or "Position" — used in the section header.
; spacing: object with afterHeader, afterKey, afterClick, afterWait keys.
; timerArray: the array to push new timer objects into.
; addPositionFn: optional callback for adding position controls.
; Returns the final contentY value.

CreateTimerRows(tabIndex, gui, startY, numTimers, timerType, spacing, timerArray, addPositionFn := "") {
    global Layout, ModeDDL

    contentY := startY
    Loop numTimers {
        i := A_Index

        ; Create the per-timer object
        timerObj := {clickEdit: "", waitEdit: "", modeDDL: "", buttonDDL: "", keyEdit: "", sharedLabel: "", posEdit: "", posLabel: "", posButton: "", currentMode: "Click", cachedClickMs: 0, cachedWaitMs: 0, cachedKey: "", cachedKeyValid: false, cachedClickButton: "Left", cachedPosition: "0,0"}

        ; Section header
        AddLabel(tabIndex, gui, Layout.sectionHeaderX, contentY, ModeDDL.x - Layout.sectionHeaderX - 5, "═══ Timer " i " (" timerType ") ═══", "h" Layout.sectionHeaderH " cBlue")

        ; Mode dropdown + shared slot (populates timerObj.modeDDL, .buttonDDL, .keyEdit, .sharedLabel)
        keyRowY := contentY + spacing.afterHeader
        AddModeAndKeyControls(tabIndex, gui, contentY, keyRowY, timerObj)
        contentY += spacing.afterHeader + spacing.afterKey

        ; Click Duration (no timer number in label)
        AddLabel(tabIndex, gui, Layout.contentLeft, contentY, Layout.labelWidth, "Hold (ms):")
        timerObj.clickEdit := AddEditWithUpDown(tabIndex, gui, Layout.editLeft, contentY, Layout.editWidth, 0)
        timerObj.clickEdit.OnEvent("Change", OnTimerControlChange.Bind(timerObj))
        contentY += spacing.afterClick

        ; Wait Duration (no timer number in label)
        AddLabel(tabIndex, gui, Layout.contentLeft, contentY, Layout.labelWidth, "Wait (ms):")
        timerObj.waitEdit := AddEditWithUpDown(tabIndex, gui, Layout.editLeft, contentY, Layout.editWidth, 0)
        timerObj.waitEdit.OnEvent("Change", OnTimerControlChange.Bind(timerObj))
        contentY += spacing.afterWait

        ; Position controls (only for position timers)
        if (addPositionFn != "")
            contentY := addPositionFn.Call(tabIndex, gui, contentY, i, timerObj)

        ; Initialize the value cache now that all controls exist on this timer.
        RefreshTimerCache(timerObj)

        timerArray.Push(timerObj)
    }
    return contentY
}

; ==================== HELPER: ADD POSITION ROW ====================
; Adds the Position (X,Y) edit + Get Pos button for a single position timer.
; posEdits/posButtons are the set-level arrays for backward compat with GetPosition.
; timerObj receives the posEdit, posLabel, posButton references.
; Returns the updated contentY.

AddPositionRow(posEdits, posButtons, getPosFn, tabIndex, gui, contentY, timerIndex, timerObj) {
    global Layout, PosBtn, PosSpacing, Defaults

    posLabel := AddLabel(tabIndex, gui, Layout.contentLeft, contentY, Layout.labelWidth, "Position (X,Y):")

    editCtrl := gui.Add("Edit", "x" Layout.editLeft " y" contentY " w" Layout.editWidth " ReadOnly", Defaults.position)
    posEdits.Push(editCtrl)
    timerObj.posEdit := editCtrl
    timerObj.posLabel := posLabel
    RegisterControl(tabIndex, editCtrl, contentY)

    btn := gui.Add("Button", "x" PosBtn.x " y" (contentY + PosBtn.yOffset) " w" PosBtn.w " h" PosBtn.h, "Get Pos")
    btn.OnEvent("Click", getPosFn.Bind(timerIndex))
    posButtons.Push(btn)
    timerObj.posButton := btn
    RegisterControl(tabIndex, btn, contentY + PosBtn.yOffset)

    ; Stamp controls for O(1) mode-visibility lookup
    editCtrl.isPosEdit := true
    editCtrl.timerObj := timerObj
    posLabel.isPosLabel := true
    posLabel.timerObj := timerObj
    btn.isPosButton := true
    btn.timerObj := timerObj

    return contentY + PosSpacing.afterPos
}

; ==================== HELPER: CREATE GENERAL TIMERS ====================
; Creates all general timer controls for the General Timers tab
; Returns the final contentY value

CreateGeneralTimers(tabIndex, gui, startY, numTimers, timerArray) {
    global GenSpacing
    spacing := {afterHeader: GenSpacing.afterHeader, afterKey: GenSpacing.afterKey, afterClick: GenSpacing.afterClick, afterWait: GenSpacing.afterWait}
    return CreateTimerRows(tabIndex, gui, startY, numTimers, "General", spacing, timerArray)
}

; ==================== HELPER: CREATE POSITION TIMERS ====================
; Creates all position timer controls for a tab
; Returns the final contentY value

CreatePositionTimers(tabIndex, gui, startY, numTimers, posEdits, posButtons, getPosFn, timerArray) {
    global PosSpacing
    spacing := {afterHeader: PosSpacing.afterHeader, afterKey: PosSpacing.afterKey, afterClick: PosSpacing.afterClick, afterWait: PosSpacing.afterWait}
    addPosFn := AddPositionRow.Bind(posEdits, posButtons, getPosFn)
    return CreateTimerRows(tabIndex, gui, startY, numTimers, "Position", spacing, timerArray, addPosFn)
}

; ==================== HELPER: BUILD POSITION TAB ====================
; Creates the interval control and position timers for a position tab.
; Returns the seconds edit control.

BuildPositionTab(tabIndex, gui, numTimers, posSet, getPosFn) {
    global TabLayout, IntervalLayout, ScrollState

    contentY := TabLayout.contentTop + TabLayout.clipPadding + TabLayout.contentInset

    ; In-tab interval control
    secondsEdit := AddIntervalControl(tabIndex, gui, contentY)
    contentY += IntervalLayout.after

    ; Create all position timers
    contentY := CreatePositionTimers(tabIndex, gui, contentY, numTimers, posSet.posEdits, posSet.posButtons, getPosFn, posSet.timers)
    ScrollState.totalContentHeight[tabIndex] := contentY - TabLayout.contentTop

    ; Cache the interval value and refresh on user edits so the main
    ; loop (TryRunPositionSets) can read posSet.cachedIntervalMs without
    ; a Win32 round-trip every tick. Empty value (user cleared the field)
    ; coerces to 0 to avoid Integer("") throwing.
    intervalVal := secondsEdit.Value
    posSet.cachedIntervalMs := (intervalVal = "") ? 0 : Integer(intervalVal)
    secondsEdit.OnEvent("Change", (*) => posSet.cachedIntervalMs := (secondsEdit.Value = "") ? 0 : Integer(secondsEdit.Value))

    return secondsEdit
}

; ==================== CREATE MAIN GUI ====================
MyGui := Gui("+AlwaysOnTop", "InFeRiOr's Input Automator v3.5.1")
MyGui.SetFont(Fonts.default)
MyGui.Opt("+LastFound")

; Instructions at top
MyGui.Add("Text", "x" Window.marginX " y" Header.y " w" Window.contentWidth " h" Header.height " Center", "Configure up to " TOTAL_TIMERS " click and/or keypress cycles.")

; Create Tab control with 4 tabs
tabs := MyGui.Add("Tab3", "x" TabLayout.x " y" TabLayout.y " w" TabLayout.width " h" TabLayout.height, ["General Timers", "Position Timers 1", "Position Timers 2", "Position Timers 3"])

; ==================== GENERAL TIMERS TAB ====================
tabs.UseTab(1)

contentY := TabLayout.contentTop + TabLayout.clipPadding + TabLayout.contentInset
contentY := CreateGeneralTimers(1, MyGui, contentY, Timers.numBasic, generalTimers)
ScrollState.totalContentHeight[1] := contentY - TabLayout.contentTop

; ==================== POSITION TIMERS (FIRST) TAB ====================
tabs.UseTab(2)
positionSets[1].secondsEdit := BuildPositionTab(2, MyGui, Timers.numPosition1, positionSets[1], GetPosition)

; ==================== POSITION TIMERS (SECOND) TAB ====================
tabs.UseTab(3)
positionSets[2].secondsEdit := BuildPositionTab(3, MyGui, Timers.numPosition2, positionSets[2], GetPosition)

; ==================== POSITION TIMERS (THIRD) TAB ====================
tabs.UseTab(4)
positionSets[3].secondsEdit := BuildPositionTab(4, MyGui, Timers.numPosition3, positionSets[3], GetPosition)

; End tab definition
tabs.UseTab()

; ==================== BOTTOM CONTROLS (outside tabs) ====================
statusText := MyGui.Add("Text", "x" Window.marginX " y" BottomUI.statusY " w" Window.contentWidth " h" BottomUI.statusH " Center", "Status: Stopped")
statusText.SetFont(Fonts.status)

startStopBtn := MyGui.Add("Button", "x" Window.marginX " y" BottomUI.btnY " w" BottomUI.btnLeftW " h" BottomUI.btnH, "START (F6)")
startStopBtn.SetFont(Fonts.btn)
startStopBtn.OnEvent("Click", ToggleScript)

resetBtn := MyGui.Add("Button", "x" BottomUI.btnRightX " y" BottomUI.btnY " w" BottomUI.btnRightW " h" BottomUI.btnH, "RESET")
resetBtn.SetFont(Fonts.btn)
resetBtn.OnEvent("Click", ResetAll)

MyGui.Add("Text", "x" Window.marginX " y" BottomUI.footerLine1Y " w" Window.contentWidth " Center", "Hotkeys: F6 = Start/Stop | F7 = Exit")
MyGui.Add("Text", "x" Window.marginX " y" BottomUI.footerLine1bY " w" Window.contentWidth " Center", 'Key Name: + = combo | "text" = type verbatim')
MyGui.Add("Text", "x" Window.marginX " y" BottomUI.footerLine2Y " w" Window.contentWidth " Center cRed", "Each position tab has its own execution interval.")

MyGui.Show("w" Window.width " h" Window.height)

; Apply initial scroll clipping
Loop NUM_TABS
    ApplyScrollClipping(A_Index)

; ==================== INSTALL LOW-LEVEL MOUSE HOOK ====================
InstallMouseHook()

; ==================== HANDLE CUSTOM SCROLL MESSAGE ====================
OnMessage(WM_APP_SCROLL, OnAppScroll)

OnAppScroll(wParam, lParam, msg, hwnd) {
    global tabs, ScrollState, TabLayout

    Critical

    currentTab := tabs.Value

    ; wParam contains mouseData — high word is the wheel delta
    delta := (wParam >> 16) & 0xFFFF
    if (delta > 32767)
        delta := delta - 65536

    maxScroll := Max(0, ScrollState.totalContentHeight[currentTab] - TAB_VISIBLE_HEIGHT)

    if (delta > 0)
        ScrollState.offset[currentTab] := Max(0, ScrollState.offset[currentTab] - TabLayout.scrollStep)
    else
        ScrollState.offset[currentTab] := Min(maxScroll, ScrollState.offset[currentTab] + TabLayout.scrollStep)

    RepositionControls(currentTab)
    return 0
}

; ==================== SCROLL HELPER FUNCTIONS ====================

; Determines if a control should be hidden due to its mode dropdown being set to "Click" or "KeyPress".
; Uses stamped properties (isButtonDDL, isKeyEdit, isPosEdit, isPosLabel, isPosButton, timerObj) for O(1) lookup
; instead of scanning arrays. The shared label carries no stamp, so it is never
; hidden by mode — only by scroll clipping.
IsHiddenByMode(ctrl) {
    ; Mouse Button dropdown: hidden when mode is KeyPress
    if (HasProp(ctrl, "isButtonDDL") && ctrl.isButtonDDL) {
        return (ctrl.timerObj.currentMode = "KeyPress")
    }
    ; Key edit: hidden when mode is Click
    if (HasProp(ctrl, "isKeyEdit") && ctrl.isKeyEdit) {
        return (ctrl.timerObj.currentMode = "Click")
    }
    ; Position edit, label, button: hidden when mode is KeyPress
    if (HasProp(ctrl, "isPosEdit") && ctrl.isPosEdit) {
        return (ctrl.timerObj.currentMode = "KeyPress")
    }
    if (HasProp(ctrl, "isPosLabel") && ctrl.isPosLabel) {
        return (ctrl.timerObj.currentMode = "KeyPress")
    }
    if (HasProp(ctrl, "isPosButton") && ctrl.isPosButton) {
        return (ctrl.timerObj.currentMode = "KeyPress")
    }
    return false
}

RepositionControls(tabIndex) {
    global ScrollState, Win32, TabLayout

    offset  := ScrollState.offset[tabIndex]
    clipTop := TabLayout.contentTop + TabLayout.clipPadding
    clipBot := TabLayout.contentBottom - TabLayout.clipPadding

    ; If the offset hasn't changed since the last apply, only visibility may
    ; need updating (e.g. after a mode toggle). Skip the Move calls in that
    ; case — they're the dominant Win32 cost.
    offsetChanged := (ScrollState.lastAppliedOffset[tabIndex] != offset)

    ; Suspend redrawing to prevent flicker
    DllCall("SendMessage", "Ptr", MyGui.Hwnd, "UInt", Win32.wmSetRedraw, "Ptr", 0, "Ptr", 0)

    for ctrl in ScrollState.tabControls[tabIndex] {
        origY := ScrollState.controlOrigY[ctrl.Hwnd]
        newY  := origY - offset

        if (offsetChanged)
            ctrl.Move(, newY)

        ; Cache control height on first access — heights never change after creation
        if (!ScrollState.controlHeight.Has(ctrl.Hwnd)) {
            ctrl.GetPos(, , , &ch)
            ScrollState.controlHeight[ctrl.Hwnd] := ch
        } else {
            ch := ScrollState.controlHeight[ctrl.Hwnd]
        }

        inScrollView := (newY >= clipTop && (newY + ch) <= clipBot)
        shouldBeVisible := (inScrollView && !IsHiddenByMode(ctrl))

        ; Only toggle Visible when the state actually changes — each toggle
        ; is a Win32 round-trip, and most controls don't change state per scroll.
        if (!ScrollState.lastVisible.Has(ctrl.Hwnd) || ScrollState.lastVisible[ctrl.Hwnd] != shouldBeVisible) {
            ctrl.Visible := shouldBeVisible
            ScrollState.lastVisible[ctrl.Hwnd] := shouldBeVisible
        }
    }

    ScrollState.lastAppliedOffset[tabIndex] := offset

    ; Resume redrawing and repaint only the tab area
    DllCall("SendMessage", "Ptr", MyGui.Hwnd, "UInt", Win32.wmSetRedraw, "Ptr", 1, "Ptr", 0)

    rect := Buffer(16, 0)
    NumPut("Int", TabLayout.x,                       rect, 0)
    NumPut("Int", TabLayout.y,                       rect, 4)
    NumPut("Int", TabLayout.x + TabLayout.width,     rect, 8)
    NumPut("Int", TabLayout.y + TabLayout.height,    rect, 12)
    DllCall("RedrawWindow", "Ptr", MyGui.Hwnd, "Ptr", rect, "Ptr", 0, "UInt", Win32.rdwFlags)
}

ApplyScrollClipping(tabIndex) {
    RepositionControls(tabIndex)
}

; Reset scroll when switching tabs
tabs.OnEvent("Change", TabChanged)
TabChanged(GuiCtrlObj, *) {
    RepositionControls(GuiCtrlObj.Value)
}

; ==================== HOTKEYS ====================
F6::ToggleScript()
F7::{
    ReleaseAllKeys()
    RemoveMouseHook()
    ExitApp()
}

; ==================== GET POSITION (UNIFIED) ====================
; Single function to handle position capture for any position edit array
; The timerIndex parameter is bound when the button is created
; FIX #3: Wrapped in try/finally so the system cursor is always restored,
; even if the user exits or an error occurs during capture.

GetPosition(timerIndex, *) {
    global positionSets, tabs, CursorConst

    ; Determine which position array to use based on current tab
    ; Tab 2 = position set 1, Tab 3 = position set 2, Tab 4 = position set 3
    currentTab := tabs.Value
    setIndex := currentTab - 1
    if (setIndex < 1 || setIndex > 3)
        return  ; Safety check: only position tabs (2, 3, 4) are valid

    posEdits := positionSets[setIndex].posEdits

    cursorHandle := DllCall("LoadCursor", "Ptr", 0, "Int", CursorConst.idcCross, "Ptr")
    DllCall("SetSystemCursor", "Ptr", cursorHandle, "Int", CursorConst.ocrNormal)

    try {
        KeyWait("LButton", "D")
        MouseGetPos(&xPos, &yPos)

        posEdits[timerIndex].Value := xPos "," yPos

        ; Programmatic Edit.Value writes do not fire Change in AHK v2,
        ; so manually refresh the owning timer's cache. The timerObj is
        ; stamped on the posEdit when the row is created.
        if (HasProp(posEdits[timerIndex], "timerObj"))
            RefreshTimerCache(posEdits[timerIndex].timerObj)

        ToolTip("Position captured: " xPos "," yPos)
        SetTimer(() => ToolTip(), -TOOLTIP_TIMEOUT)
    } finally {
        ; Always restore the system cursor, even if interrupted or errored
        DllCall("SystemParametersInfo", "UInt", CursorConst.spiSet, "UInt", 0, "Ptr", 0, "UInt", 0)
    }
}

; ==================== TOGGLE START/STOP ====================
; FIX #4: Explicitly cancel the timer with SetTimer(..., 0) on stop,
; so the loop callback is immediately deregistered rather than relying
; on the next tick to see isRunning=false and self-cancel.

ToggleScript(*) {
    global AppState, Timers

    if (AppState.isRunning) {
        AppState.isRunning := false
        SetTimer(MainClickLoop, 0)  ; FIX #4: Immediately cancel the timer
        ReleaseAllKeys()             ; Release any held keys/buttons on stop
        startStopBtn.Text := "START (F6)"
        statusText.Text := "Status: Stopped"
        statusText.SetFont("cBlack")
    } else {
        ; Validate all KeyPress timers before starting
        if (!ValidateAllKeyTimers())
            return

        AppState.isRunning := true
        Loop 3
            AppState.lastPositionRun[A_Index] := A_TickCount
        startStopBtn.Text := "STOP (F6)"
        statusText.Text := "Status: Running"
        statusText.SetFont("cGreen")
        SetTimer(MainClickLoop, Timers.loopInterval)
    }
}

; ==================== RESET ALL ====================
ResetAll(*) {
    global AppState, generalTimers, positionSets, ScrollState, Defaults, MouseBtn

    if (AppState.isRunning)
        ToggleScript()

    Loop 3 {
        positionSets[A_Index].secondsEdit.Value := Defaults.seconds
        ; Sync interval cache: programmatic Edit.Value writes do not
        ; fire Change in AHK v2.
        positionSets[A_Index].cachedIntervalMs := Integer(Defaults.seconds)
    }

    ; Reset general timers
    for t in generalTimers {
        t.clickEdit.Value := Defaults.clickMs
        t.waitEdit.Value := Defaults.waitMs
        t.modeDDL.Choose(1)      ; Reset to Click
        t.keyEdit.Value := Defaults.key
        t.buttonDDL.Choose(1)    ; Reset Mouse Button to Left
        t.sharedLabel.Text := MouseBtn.label   ; Back to "Mouse Button:" (Click mode)
        ; Sync caches: Choose() and programmatic Value writes do not
        ; fire Change in AHK v2, so we must update them manually.
        ; Visibility is recomputed by RepositionControls below based on
        ; currentMode, so direct Visible writes are unnecessary here
        ; (and would desync the lastVisible cache).
        t.currentMode := "Click"
        RefreshTimerCache(t)
    }

    ; Reset position timers
    Loop 3 {
        for t in positionSets[A_Index].timers {
            t.clickEdit.Value := Defaults.clickMs
            t.waitEdit.Value := Defaults.waitMs
            t.modeDDL.Choose(1)      ; Reset to Click
            t.keyEdit.Value := Defaults.key
            t.buttonDDL.Choose(1)    ; Reset Mouse Button to Left
            t.sharedLabel.Text := MouseBtn.label   ; Back to "Mouse Button:" (Click mode)
            ; Sync caches: see comment in general timer reset above.
            t.currentMode := "Click"
        }
        for edit in positionSets[A_Index].posEdits
            edit.Value := Defaults.position
        ; Refresh caches for this set's timers AFTER all posEdits are reset
        ; so cachedPosition picks up the new "0,0" default.
        for t in positionSets[A_Index].timers
            RefreshTimerCache(t)
    }

    Loop NUM_TABS {
        ScrollState.offset[A_Index] := 0
    }
    RepositionControls(tabs.Value)

    ToolTip("All values reset!")
    SetTimer(() => ToolTip(), -TOOLTIP_TIMEOUT)
}

; ==================== CLICK HELPERS ====================
; clickBtn is the per-timer Mouse Button selection ("Left", "Right", or "Middle"),
; passed in from the caller's cached value.
ClickAtPosition(x, y, holdDuration, clickBtn) {
    global AppState, ClickTiming
    DllCall("SetCursorPos", "Int", x, "Int", y)
    if (!InterruptibleSleep(ClickTiming.preDelay))
        return false
    Click x " " y " " clickBtn " Down"
    if (!InterruptibleSleep(holdDuration)) {
        ; Release the mouse button even if interrupted mid-hold
        Click x " " y " " clickBtn " Up"
        return false
    }
    Click x " " y " " clickBtn " Up"
    return true
}

; ==================== KEY PRESS HELPER ====================
; Sends a key press (or key combo) with hold duration.
; Supports "+" separated combos like "LControl+V" or "Ctrl + Shift + Escape".
; Keys are pressed down in order and released in reverse order.
; A double-quote-wrapped literal (e.g. "*sleeps*") is typed verbatim via SendText.
; Returns true if completed, false if interrupted.

SendKeyPress(keyString, holdDuration) {
    global AppState

    ; Quoted literal — strip the outer quotes and type the rest verbatim.
    ; SendText does not interpret *, +, ^, !, #, {} etc., so "*sleeps*" types
    ; the characters *sleeps* literally. holdDuration does not apply to a typed
    ; string, so it is intentionally ignored here. Whether this is reached at all
    ; is still governed by the callers' existing Hold/Wait gating (unchanged).
    if (IsQuotedLiteral(keyString)) {
        SendText(SubStr(keyString, 2, StrLen(keyString) - 2))
        return true
    }

    ; Special case: literal "+" key by itself
    if (Trim(keyString) = "+") {
        if (holdDuration > 0) {
            Send("{+ down}")
            if (!InterruptibleSleep(holdDuration)) {
                Send("{+ up}")
                return false
            }
            Send("{+ up}")
        } else {
            Send("{+}")
        }
        return true
    }

    ; Split on "+" to support combos like "Ctrl+V" or "Ctrl + Shift + Escape"
    keys := StrSplit(keyString, "+")
    Loop keys.Length
        keys[A_Index] := Trim(keys[A_Index])

    ; Map modifier key names to AHK Send prefixes (all recognized variants)
    static modMap := Map(
        "ctrl", "^", "lctrl", "^", "rctrl", "^",
        "control", "^", "lcontrol", "^", "rcontrol", "^",
        "alt", "!", "lalt", "!", "ralt", "!",
        "shift", "+", "lshift", "+", "rshift", "+",
        "lwin", "#", "rwin", "#"
    )

    ; Separate modifiers from the final key
    modPrefix := ""
    finalKey := ""
    for key in keys {
        lowerKey := StrLower(key)
        if modMap.Has(lowerKey)
            modPrefix .= modMap[lowerKey]
        else
            finalKey := key
    }

    ; If no non-modifier key found, hold each modifier for the duration then release
    if (finalKey = "") {
        if (holdDuration > 0) {
            ; Press all modifiers down
            for key in keys
                Send("{" key " down}")
            if (!InterruptibleSleep(holdDuration)) {
                ; Release all modifiers even if interrupted
                for key in keys
                    Send("{" key " up}")
                return false
            }
            ; Release all modifiers
            for key in keys
                Send("{" key " up}")
        } else {
            ; Zero hold duration: just tap each modifier
            for key in keys
                Send("{" key "}")
        }
        return true
    }

    if (holdDuration > 0) {
        Send(modPrefix "{" finalKey " down}")
        if (!InterruptibleSleep(holdDuration)) {
            Send(modPrefix "{" finalKey " up}")
            return false
        }
        Send(modPrefix "{" finalKey " up}")
    } else {
        Send(modPrefix "{" finalKey "}")
    }
    return true
}

; Helper to return cursor to saved position
ReturnCursor(returnX, returnY) {
    global ClickTiming
    DllCall("SetCursorPos", "Int", returnX, "Int", returnY)
    Sleep(ClickTiming.cursorReturn1)
    MouseMove(returnX, returnY)
    Sleep(ClickTiming.cursorReturn2)
}

; ==================== RUN POSITION TIMER SET ====================
; Runs a set of position timers using per-timer objects.
; timers: array of timer objects for this set
; Returns true if completed without interruption, false if stopped.

RunPositionTimerSet(timers) {
    global AppState, ClickTiming

    for t in timers {
        if (!AppState.isRunning)
            return false

        ; Read snapshotted values from the timerObj cache instead of
        ; issuing Win32 round-trips to each control. The cache is kept
        ; in sync via Change handlers (user edits) and explicit refresh
        ; calls in ResetAll/GetPosition/OnModeChange.
        clickDuration := t.cachedClickMs
        waitDuration  := t.cachedWaitMs
        positionStr   := t.cachedPosition
        mode          := t.currentMode

        if (clickDuration = 0 && waitDuration = 0)
            continue

        ; For KeyPress mode, skip if no valid key is set
        if (mode = "KeyPress") {
            keyName := t.cachedKey
            if (keyName = "" || !t.cachedKeyValid)
                continue
            ; KeyPress mode doesn't use position, just send the key
            if (!SendKeyPress(keyName, clickDuration))
                return false
            if (waitDuration > 0) {
                if (!InterruptibleSleep(waitDuration))
                    return false
            }
            continue
        }

        ; Click mode: use position
        ; Skip positions that are still at the default 0,0 (unconfigured)
        if (positionStr = "0,0")
            continue

        posArray := StrSplit(positionStr, ",")
        if (posArray.Length != 2)
            continue

        xPos := Integer(posArray[1])
        yPos := Integer(posArray[2])

        ; Click logic — uses this timer's own cached Mouse Button selection
        if (clickDuration > 0) {
            if (!ClickAtPosition(xPos, yPos, clickDuration, t.cachedClickButton))
                return false
            if (!InterruptibleSleep(ClickTiming.postDelay))
                return false
        } else {
            DllCall("SetCursorPos", "Int", xPos, "Int", yPos)
        }

        if (waitDuration > 0) {
            if (!InterruptibleSleep(waitDuration))
                return false
        }
    }
    return true
}

; ==================== MAIN CLICK LOOP (SPLIT INTO FOCUSED FUNCTIONS) ====================

; Checks each position timer set and runs it if its interval has elapsed.
; Returns true if any position set executed this tick.

TryRunPositionSets() {
    global AppState, positionSets

    currentTime := A_TickCount
    anyRan := false

    Loop 3 {
        setIdx := A_Index
        ; Read cached interval instead of issuing a Win32 round-trip
        ; to the secondsEdit control on every loop tick.
        targetMs := positionSets[setIdx].cachedIntervalMs
        isDue := (targetMs > 0 && currentTime - AppState.lastPositionRun[setIdx] >= targetMs)

        if (isDue && AppState.isRunning) {
            RunPositionTimerSet(positionSets[setIdx].timers)
            AppState.lastPositionRun[setIdx] := A_TickCount
            anyRan := true
        }
    }

    return anyRan
}

; Executes all configured general timers (tab 1) sequentially.
; Called only when no position sets ran this tick.

RunGeneralTimers(cursorX, cursorY) {
    global AppState, generalTimers

    for t in generalTimers {
        if (!AppState.isRunning)
            break

        ; Read snapshotted values from the timerObj cache instead of
        ; issuing Win32 round-trips to each control. The cache is kept
        ; in sync via Change handlers (user edits) and explicit refresh
        ; calls in ResetAll/OnModeChange.
        clickDuration := t.cachedClickMs
        waitDuration  := t.cachedWaitMs
        mode          := t.currentMode

        if (clickDuration = 0 && waitDuration = 0)
            continue

        if (mode = "KeyPress") {
            keyName := t.cachedKey
            if (keyName = "" || !t.cachedKeyValid)
                continue

            if (clickDuration > 0) {
                if (!SendKeyPress(keyName, clickDuration))
                    break
            }
        } else {
            ; Click logic — uses this timer's own cached Mouse Button selection
            if (clickDuration > 0) {
                if (!ClickAtPosition(cursorX, cursorY, clickDuration, t.cachedClickButton))
                    break
            }
        }

        if (waitDuration > 0) {
            if (!InterruptibleSleep(waitDuration))
                break
        }
    }
}

; Main loop entry point called by SetTimer.
; Priority order: Position Sets > General Timers.
; If any position set runs, general timers are skipped for that tick.

MainClickLoop() {
    global AppState

    if (!AppState.isRunning) {
        SetTimer(MainClickLoop, 0)
        return
    }

    ; FIX #1: Always capture cursor position up front so it's never uninitialized
    MouseGetPos(&savedX, &savedY)

    ; --- Try position timer sets first ---
    anyPositionRan := TryRunPositionSets()

    ; Return cursor once if any position set moved it
    if (anyPositionRan && AppState.isRunning) {
        ReturnCursor(savedX, savedY)
    }

    ; --- General timers only if no position set ran ---
    if (!anyPositionRan && AppState.isRunning) {
        RunGeneralTimers(savedX, savedY)
    }

    if (AppState.isRunning)
        statusText.Text := "Status: Running"
}

; ==================== GUI CLOSE ====================
MyGui.OnEvent("Close", GuiClose)
GuiClose(*) {
    ReleaseAllKeys()
    RemoveMouseHook()
    ExitApp()
}
