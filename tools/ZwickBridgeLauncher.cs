using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
class ZwickBridgeLauncher {
    [STAThread] static int Main(string[] args) {
        string name=Path.GetFileNameWithoutExtension(Application.ExecutablePath).ToLowerInvariant();
        string action=name.Contains("final")?"Finalize":name.Contains("stop")?"Stop":"Start";
        string script=Path.Combine(Path.GetDirectoryName(Application.ExecutablePath),"ZwickCoupling","zwick_bridge.ps1");
        var info=new ProcessStartInfo("powershell.exe","-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \""+script+"\" -Action "+action);
        info.UseShellExecute=false;info.CreateNoWindow=true;info.RedirectStandardOutput=true;info.RedirectStandardError=true;
        using(var p=Process.Start(info)) {
            string error=p.StandardError.ReadToEnd();
            p.WaitForExit();
            if(p.ExitCode!=0) MessageBox.Show(error,"Atracsys / testXpert – command failed",MessageBoxButtons.OK,MessageBoxIcon.Error);
            return p.ExitCode;
        }
    }
}
