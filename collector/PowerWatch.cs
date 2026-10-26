using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Windows.Forms;
class PowerWatch : NativeWindow {
 [DllImport("user32.dll",SetLastError=true)] static extern IntPtr RegisterPowerSettingNotification(IntPtr h,ref Guid setting,uint flags);
 [DllImport("user32.dll")] static extern bool UnregisterPowerSettingNotification(IntPtr h);
 IntPtr registration;
 string path, state="unknown", lastEvent=null;
 JavaScriptSerializer json=new JavaScriptSerializer();
 public PowerWatch(string output){
  path=Path.GetFullPath(output);CreateHandle(new CreateParams(){Caption="MessageProbe.PowerWatch"});
  Guid guid=new Guid("6fe69556-704a-47a0-8f24-c28d936fda47");
  registration=RegisterPowerSettingNotification(Handle,ref guid,0);
  if(registration==IntPtr.Zero)throw new Exception("RegisterPowerSettingNotification failed: "+Marshal.GetLastWin32Error());
  Write("registered");
 }
 public void Write(string kind){
  string at=DateTimeOffset.Now.ToString("o");
  string line=json.Serialize(new {observed_at=at, display_state=state,last_display_event_at=lastEvent,event_kind=kind,observer_pid=System.Diagnostics.Process.GetCurrentProcess().Id});
  var temp=path+".tmp";File.WriteAllText(temp,line,new System.Text.UTF8Encoding(false));
  if(File.Exists(path))File.Replace(temp,path,null);else File.Move(temp,path);
  Console.WriteLine(line);Console.Out.Flush();
 }
 protected override void WndProc(ref Message m){
  if(m.Msg==0x218){
   if(m.WParam.ToInt64()==0x8013&&m.LParam!=IntPtr.Zero){
    Guid guid=(Guid)Marshal.PtrToStructure(m.LParam,typeof(Guid));
    if(guid==new Guid("6fe69556-704a-47a0-8f24-c28d936fda47")&&Marshal.ReadInt32(m.LParam,16)==4){
     int value=Marshal.ReadInt32(m.LParam,20);state=value==0?"off":value==1?"on":value==2?"dimmed":"unknown";
     lastEvent=DateTimeOffset.Now.ToString("o");Write("console_display_state");
    }
   }else if(m.WParam.ToInt64()==4){state="unknown";Write("system_suspend");}
   else if(m.WParam.ToInt64()==18||m.WParam.ToInt64()==7){state="unknown";Write("system_resume");}
  }
  base.WndProc(ref m);
 }
 [STAThread] static int Main(string[] args){
  try{
   Console.OutputEncoding=new System.Text.UTF8Encoding(false);
   var watcher=new PowerWatch(args[0]);var last=DateTime.UtcNow;
   while(true){Application.DoEvents();if((DateTime.UtcNow-last).TotalSeconds>=2){watcher.Write("heartbeat");last=DateTime.UtcNow;}System.Threading.Thread.Sleep(50);}
  }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}
 }
}
