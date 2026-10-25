# Put "COOP A" / "COOP B" in the titles of every window of the two test Dolphins (each Dolphin has a menu
# window and a game window, and both have the same default title). Dolphin may overwrite a title later.
param(
    [Parameter(Mandatory = $true)][int]$PidA,
    [Parameter(Mandatory = $true)][int]$PidB
)
Add-Type @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class CoopWin {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern bool SetWindowText(IntPtr h, string s);
  public static List<IntPtr> Windows(uint pid) {
    var res = new List<IntPtr>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
      if (p == pid && IsWindowVisible(h)) { var sb = new StringBuilder(256); if (GetWindowText(h, sb, 256) > 0) res.Add(h); } return true; }, IntPtr.Zero);
    return res;
  }
}
"@
foreach ($pair in @(@($PidA, 'COOP A  (host)'), @($PidB, 'COOP B  (join)'))) {
    $wins = [CoopWin]::Windows([uint32]$pair[0])
    foreach ($h in $wins) { [CoopWin]::SetWindowText($h, $pair[1]) | Out-Null }
    Write-Host "$($pair[1]): pid $($pair[0]), $($wins.Count) window(s) labelled"
}
