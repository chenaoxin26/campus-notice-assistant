using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Windows.Automation;
using Microsoft.Win32;
class Probe {
 [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
 [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint f,bool i,uint a);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
 [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h,int cmd);
 [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
 delegate bool EnumWindowProc(IntPtr h,IntPtr p);
 [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowProc cb,IntPtr p);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr h,int index,System.Text.StringBuilder text,int length,out int needed);
 [StructLayout(LayoutKind.Sequential)] struct LASTINPUTINFO {public uint cbSize;public uint dwTime;}
 [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO info);
 static long IdleMs(){var info=new LASTINPUTINFO();info.cbSize=(uint)Marshal.SizeOf(info);if(!GetLastInputInfo(ref info))return -1;return unchecked((uint)Environment.TickCount-info.dwTime);}
 static Dictionary<string,object> D(params object[] a){var d=new Dictionary<string,object>();for(int i=0;i<a.Length;i+=2)d[(string)a[i]]=a[i+1];return d;}
 static bool Desktop(){var h=OpenInputDesktop(0,false,0x101);if(h==IntPtr.Zero)return false;var name=new System.Text.StringBuilder(256);int needed;bool ok=GetUserObjectInformation(h,2,name,512,out needed);CloseDesktop(h);return ok&&String.Equals(name.ToString(),"Default",StringComparison.OrdinalIgnoreCase);}
 static object Inventory(){
  var installs=new List<object>();
  foreach(var hive in new[]{Registry.CurrentUser,Registry.LocalMachine}) foreach(var key in new[]{@"Software\Microsoft\Windows\CurrentVersion\Uninstall",@"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"}){
   using(var root=hive.OpenSubKey(key)){if(root==null)continue;foreach(var name in root.GetSubKeyNames())using(var sub=root.OpenSubKey(name)){
    if(sub==null)continue;string title=Convert.ToString(sub.GetValue("DisplayName"));
    if(title=="QQ"||title=="微信"||title=="WeChat"||title=="Weixin")installs.Add(D("name",title,"version",sub.GetValue("DisplayVersion"),"location",sub.GetValue("InstallLocation"),"icon",sub.GetValue("DisplayIcon")));
   }}
  }
  var processes=new List<object>();
  foreach(var p in Process.GetProcesses()){
   if(p.ProcessName!="QQ"&&p.ProcessName!="WeChat"&&p.ProcessName!="Weixin")continue;
   var d=D("source",p.ProcessName,"pid",p.Id,"session",p.SessionId,"handle",p.MainWindowHandle.ToInt64());
   try{string path=p.MainModule.FileName;d["path"]=path;d["version"]=FileVersionInfo.GetVersionInfo(path).ProductVersion;}catch(Exception e){d["error"]=e.Message;}
   processes.Add(d);
  }
  return D("installed",installs,"processes",processes,"os",Environment.OSVersion.VersionString,"desktop_accessible",Desktop(),"foreground_handle",GetForegroundWindow().ToInt64());
 }
 static object QQWindows(){
  var pids=new HashSet<int>();foreach(var p in Process.GetProcessesByName("QQ"))pids.Add(p.Id);
  var windows=new List<object>();
  EnumWindows(delegate(IntPtr h,IntPtr ignored){uint pid;GetWindowThreadProcessId(h,out pid);
   if(pids.Contains((int)pid)&&(IsWindowVisible(h)||IsIconic(h)))windows.Add(D("handle",h.ToInt64(),"pid",pid,"minimized",IsIconic(h),"foreground",h==GetForegroundWindow()));
   return true;
  },IntPtr.Zero);
  return D("desktop_accessible",Desktop(),"windows",windows);
 }
 static object Locate(long handle,string target){
  if(!Desktop())throw new Exception("Desktop unavailable.");
  var root=AutomationElement.FromHandle(new IntPtr(handle));
  var found=root.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.NameProperty,target));
  var hits=new List<object>();
  for(int i=0;i<found.Count;i++){
   var el=found[i];var parents=new List<object>();var ancestor=el;
   for(int j=0;j<9&&ancestor!=null;j++){
    var c=ancestor.Current;var pp=new List<string>();foreach(var p in ancestor.GetSupportedPatterns())pp.Add(p.ProgrammaticName);
    parents.Add(D("level",j,"type",c.ControlType.ProgrammaticName,"class",c.ClassName,"automation_id",c.AutomationId,"name_is_target",c.Name==target,"offscreen",c.IsOffscreen,"patterns",pp,"container_label",c.ControlType==ControlType.Window?c.Name:null));
    ancestor=TreeWalker.RawViewWalker.GetParent(ancestor);
   }
   hits.Add(D("match",i,"ancestors",parents));
  }
  return D("target",target,"matches",hits);
 }
 // Chromium may expose a native renderer plus an MSAA compatibility copy.
 // Select the unique direct native document, never an arbitrary matching label.
 static AutomationElement QQDocument(AutomationElement window){
  var docs=new List<AutomationElement>();var walker=TreeWalker.RawViewWalker;
  var child=walker.GetFirstChild(window);
  while(child!=null){
   if(child.Current.ControlType==ControlType.Document&&child.Current.ClassName=="Chrome_RenderWidgetHostHWND")docs.Add(child);
   child=walker.GetNextSibling(child);
  }
  if(docs.Count>1)throw new Exception("QQ_NATIVE_DOCUMENT_AMBIGUOUS: multiple direct renderer documents; refusing capture.");
  return docs.Count==1?docs[0]:window;
 }
 static bool RawWithin(AutomationElement el,AutomationElement root){
  for(int i=0;i<70&&el!=null;i++){
   if(Automation.Compare(el,root))return true;
   el=TreeWalker.RawViewWalker.GetParent(el);
  }
  return false;
 }
 static List<AutomationElement> ScopedFind(AutomationElement root,ControlType type,string name){
  // Provider FindAll may return compatibility aliases outside the raw branch.
  // Walk the selected branch itself instead; bounded failures never return partial results.
  var result=new List<AutomationElement>();var q=new Queue<AutomationElement>();q.Enqueue(root);
  var timer=Stopwatch.StartNew();int count=0;
  while(q.Count>0){
   if(++count>3000||timer.Elapsed.TotalSeconds>15)throw new Exception("QQ_ACCESSIBILITY_SCAN_INCOMPLETE: scoped scan exceeded limit; refusing partial selection.");
   var el=q.Dequeue();var current=el.Current;
   if(current.ControlType==type&&current.Name==name)result.Add(el);
   var child=TreeWalker.RawViewWalker.GetFirstChild(el);
   while(child!=null){
    if(q.Count>=3000||timer.Elapsed.TotalSeconds>15)throw new Exception("QQ_ACCESSIBILITY_SCAN_INCOMPLETE: scoped scan exceeded limit.");
    q.Enqueue(child);child=TreeWalker.RawViewWalker.GetNextSibling(child);
   }
  }
  return result;
 }
 static object ReadQQ(long handle,string target,bool debug=false){
  if(!Desktop())throw new Exception("Desktop unavailable; no capture.");
  var window=AutomationElement.FromHandle(new IntPtr(handle));
  if(Process.GetProcessById(window.Current.ProcessId).ProcessName!="QQ")throw new Exception("Not a QQ window.");
  var document=QQDocument(window);
  var editors=ScopedFind(document,ControlType.Edit,target);
  if(editors.Count!=1)throw new Exception("Selected conversation not verified: exact target composer missing or ambiguous.");
  var editor=editors[0];var walker=TreeWalker.RawViewWalker;
  var rich=walker.GetParent(editor);
  if(rich.Current.Name!="Rich Text Editor")throw new Exception("QQ layout changed: composer container mismatch.");
  var scope=walker.GetParent(walker.GetParent(rich));
  if(debug)return Snapshot(handle,false,target,walker.GetParent(scope));
  var header=walker.GetFirstChild(walker.GetParent(scope));
  if(header==null||header.FindFirst(TreeScope.Descendants,new PropertyCondition(AutomationElement.NameProperty,target))==null)throw new Exception("QQ layout/target header mismatch; scope="+scope.Current.ControlType.ProgrammaticName+", previous="+(header==null?"null":header.Current.ControlType.ProgrammaticName)+"; refusing capture.");
  var messages=walker.GetNextSibling(header);
  if(messages==null||messages.Current.ControlType!=ControlType.Custom)throw new Exception("Message pane missing.");
  var list=messages.FindFirst(TreeScope.Descendants,new AndCondition(new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Window),new PropertyCondition(AutomationElement.NameProperty,"消息列表")));
  if(list==null)throw new Exception("QQ message list missing; capture refused.");
  var composer=walker.GetNextSibling(messages);
  if(composer==null||!Automation.Compare(composer,scope))throw new Exception("QQ layout changed: message/composer boundary mismatch.");
  var result=(Dictionary<string,object>)Snapshot(handle,true,target,messages);
  if(!Desktop()||editor.Current.Name!=target||header.FindFirst(TreeScope.Descendants,new PropertyCondition(AutomationElement.NameProperty,target))==null)throw new Exception("Desktop/conversation changed while reading; discarded.");
  result["source"]="QQ";result["conversation"]=target;result["scope_verified"]=true;
  return result;
 }
 static AutomationElement PreviewRow(long handle,string target){
  if(!Desktop())throw new Exception("Desktop unavailable.");
  var window=AutomationElement.FromHandle(new IntPtr(handle));
  if(Process.GetProcessById(window.Current.ProcessId).ProcessName!="QQ")throw new Exception("Not a QQ window.");
  var document=QQDocument(window);
  var lists=ScopedFind(document,ControlType.Window,"会话列表");
  if(lists.Count!=1)throw new Exception("QQ_CONVERSATION_LIST_UNAVAILABLE: conversation list missing or ambiguous in renderer document.");
  var list=lists[0];
  var found=ScopedFind(list,ControlType.Text,target);
  if(found.Count!=1)throw new Exception("QQ_GROUP_LABEL_NOT_UNIQUE: exact group label count="+found.Count+" in renderer conversation list; group may be absent, virtualized or ambiguous.");
  var walker=TreeWalker.RawViewWalker;
  var row=walker.GetParent(walker.GetParent(found[0]));
  if(!Automation.Compare(walker.GetParent(row),list))throw new Exception("Preview row boundary changed; refusing broader capture.");
  return row;
 }
 static object ActivateQQ(long handle,string target,int idleSeconds){
  if(idleSeconds<0)throw new Exception("Invalid idle threshold.");
  long idle=IdleMs();if(idle<0||idle<idleSeconds*1000L)throw new Exception("USER_ACTIVE: navigation deferred; idle_ms="+idle);
  var row=PreviewRow(handle,target);
  object pattern;if(!row.TryGetCurrentPattern(InvokePattern.Pattern,out pattern))throw new Exception("Selected conversation row has no InvokePattern.");
  var before=D("foreground_handle",GetForegroundWindow().ToInt64(),"minimized",IsIconic(new IntPtr(handle)),"idle_ms",IdleMs());
  if(!Desktop()||IdleMs()<idleSeconds*1000L)throw new Exception("Desktop/user input changed; navigation refused.");
  ((InvokePattern)pattern).Invoke();
  return D("conversation",target,"invoked",true,"selection_verified",false,"before",before,"unread_may_change",true,"foreground_after",GetForegroundWindow().ToInt64(),"minimized_after",IsIconic(new IntPtr(handle)));
 }
 static object ReadPreview(long handle,string target){
  var row=PreviewRow(handle,target);
  var result=(Dictionary<string,object>)Snapshot(handle,true,target,row);
  if(row.FindFirst(TreeScope.Descendants,new PropertyCondition(AutomationElement.NameProperty,target))==null||!Desktop())throw new Exception("Preview target changed.");
  result["source"]="QQ";result["conversation"]=target;result["record_kind"]="conversation_preview_only";
  result["complete_message"]=false;return result;
 }
 static object Snapshot(long handle,bool text,string target,AutomationElement scopedRoot=null){
  if(!Desktop())throw new Exception("Input desktop inaccessible (locked, secure desktop or denied); capture refused.");
  var h=new IntPtr(handle);if(!IsWindow(h))throw new Exception("Stale or invalid window handle.");
  var window=AutomationElement.FromHandle(h);var root=scopedRoot??window;var nodes=new List<object>();
  var q=new Queue<Tuple<AutomationElement,int,int>>();q.Enqueue(Tuple.Create(root,0,-1));var timer=Stopwatch.StartNew();bool truncated=false;
  while(q.Count>0){
   if(nodes.Count>=2000||timer.Elapsed.TotalSeconds>15){truncated=true;break;}
   var item=q.Dequeue();int index=nodes.Count;var el=item.Item1;
   try{
    var c=el.Current;var patterns=new List<string>();foreach(var p in el.GetSupportedPatterns())patterns.Add(p.ProgrammaticName);
    var d=D("index",index,"parent",item.Item3,"depth",item.Item2,"type",c.ControlType.ProgrammaticName,"class",c.ClassName,"automation_id",c.AutomationId,"name_length",c.Name.Length,"offscreen",c.IsOffscreen,"password",c.IsPassword,"patterns",patterns,"runtime_id",el.GetRuntimeId());
    if(target!=null)d["target_match"]=c.Name==target;
    if(text&&!c.IsPassword){d["name"]=c.Name;object p;
     if(el.TryGetCurrentPattern(TextPattern.Pattern,out p))try{d["text"]=((TextPattern)p).DocumentRange.GetText(32768);}catch(Exception e){d["text_error"]=e.Message;}
     if(el.TryGetCurrentPattern(ValuePattern.Pattern,out p))try{d["value"]=((ValuePattern)p).Current.Value;}catch(Exception e){d["value_error"]=e.Message;}
    }
    nodes.Add(d);
    if(item.Item2>=25){truncated=true;continue;}
    var child=TreeWalker.RawViewWalker.GetFirstChild(el);
    while(child!=null){q.Enqueue(Tuple.Create(child,item.Item2+1,index));if(q.Count>=4000){truncated=true;break;}child=TreeWalker.RawViewWalker.GetNextSibling(child);}
   }catch(Exception e){nodes.Add(D("index",index,"parent",item.Item3,"error",e.Message));}
  }
  return D("handle",handle,"pid",window.Current.ProcessId,"foreground",GetForegroundWindow()==h,"minimized",IsIconic(h),"text_included",text,"nodes",nodes,"truncated",truncated,"desktop_accessible",true);
 }
 [STAThread] static int Main(string[] args){
  try{
   object result;
   if(args.Length==0||args[0]=="inventory")result=Inventory();
   else if(args[0]=="qq-windows")result=QQWindows();
   else if(args[0]=="desktop-state")result=D("desktop_accessible",Desktop(),"foreground_handle",GetForegroundWindow().ToInt64(),"idle_ms",IdleMs());
   else if(args[0]=="snapshot")result=Snapshot(long.Parse(args[1]),false,args.Length>2?args[2]:null);
   else if(args[0]=="locate")result=Locate(long.Parse(args[1]),args[2]);
   else if(args[0]=="read-qq")result=ReadQQ(long.Parse(args[1]),args[2]);
   else if(args[0]=="read-preview")result=ReadPreview(long.Parse(args[1]),args[2]);
   else if(args[0]=="activate-qq")result=ActivateQQ(long.Parse(args[1]),args[2],args.Length>3?int.Parse(args[3]):60);
   else if(args[0]=="qq-structure")result=ReadQQ(long.Parse(args[1]),args[2],true);
   else if(args[0]=="state"){
    long h=long.Parse(args[1]);if(!Desktop()||!IsWindow(new IntPtr(h)))throw new Exception("Desktop/window unavailable.");
    if(args[2]=="minimize")ShowWindow(new IntPtr(h),6);
    else if(args[2]=="restore")ShowWindow(new IntPtr(h),9);
    else if(args[2]=="foreground"){ShowWindow(new IntPtr(h),9);SetForegroundWindow(new IntPtr(h));}
    else throw new Exception("State must be minimize or foreground.");
    result=D("foreground",GetForegroundWindow().ToInt64(),"minimized",IsIconic(new IntPtr(h)));
   }else throw new Exception("Unknown command.");
   var json=new JavaScriptSerializer();json.MaxJsonLength=8*1024*1024;
   Console.OutputEncoding=new System.Text.UTF8Encoding(false);
   Console.WriteLine(json.Serialize(D("collected_at",DateTimeOffset.Now.ToString("o"),"result",result)));return 0;
  }catch(Exception e){Console.Error.WriteLine(e.GetType().Name+": "+e.Message);return 1;}
 }
}
