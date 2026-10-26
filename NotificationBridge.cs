using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Windows.UI.Notifications;
using Windows.UI.Notifications.Management;

class NotificationBridge {
 static Dictionary<string,object> D(params object[] args){var r=new Dictionary<string,object>();for(int i=0;i<args.Length;i+=2)r[(string)args[i]]=args[i+1];return r;}
 [STAThread] static int Main(string[] args){
  Console.OutputEncoding=new UTF8Encoding(false);var json=new JavaScriptSerializer();json.MaxJsonLength=2097152;
  try {
   if(args.Length!=1)throw new Exception("Pass a JSON config containing explicitly selected groups.");
   var config=json.DeserializeObject(File.ReadAllText(args[0],Encoding.UTF8)) as Dictionary<string,object>;
   var groups=new HashSet<string>();foreach(object s in (object[])config["groups"])groups.Add((string)s);
   var ids=new HashSet<string>();if(config.ContainsKey("qq_app_ids"))foreach(object s in (object[])config["qq_app_ids"])ids.Add((string)s);
   var listener=UserNotificationListener.Current;var access=listener.GetAccessStatus();
   if(access!=UserNotificationListenerAccessStatus.Allowed){Console.WriteLine(json.Serialize(D("ok",false,"access_status",access.ToString(),"error","Notification access not allowed; no permission request or settings changes made.")));return 2;}
   var op=listener.GetNotificationsAsync(NotificationKinds.Toast);var start=DateTime.UtcNow;
   while(op.Status==Windows.Foundation.AsyncStatus.Started){if((DateTime.UtcNow-start).TotalSeconds>12)throw new TimeoutException("Windows notification enumeration timed out");Thread.Sleep(20);}
   var items=op.GetResults();var rows=new List<object>();int other=0,unavailable=0;
   // Indexed access: some classic desktop notifications have unimplemented AppInfo.
   for(int i=0;i<items.Count;i++){
    var item=items[i];string appId=null,appName=null,identityError=null;
    try {appId=item.AppInfo.AppUserModelId;}catch(Exception e){identityError=e.GetType().Name+" HRESULT="+e.HResult;}
    try {appName=item.AppInfo.DisplayInfo.DisplayName;}catch(Exception e){identityError=e.GetType().Name+" HRESULT="+e.HResult;}
    bool qq=appName=="QQ"||(appId!=null&&ids.Contains(appId));
    if(!qq&&identityError==null){other++;continue;}
    try {
     var binding=item.Notification.Visual.GetBinding(KnownNotificationBindings.ToastGeneric);
     if(binding==null){unavailable++;continue;}
     var texts=binding.GetTextElements();if(texts.Count==0||!groups.Contains(texts[0].Text)){other++;continue;}
     var all=new List<string>();var body=new List<string>();for(int j=0;j<texts.Count;j++){all.Add(texts[j].Text);if(j>0)body.Add(texts[j].Text);}
     rows.Add(D("notification_id",item.Id,"notification_created_at",item.CreationTime.ToUniversalTime().ToString("o"),
      "app_id",appId,"app_name",appName,"source_verified",qq,"identity_error",identityError,
      "group",texts[0].Text,"body",String.Join("\n",body),"texts",all,"sender",null,
      "content_completeness","notification_only_unknown","scope_verified",true));
    }catch(Exception){unavailable++;}
   }
   Console.WriteLine(json.Serialize(D("ok",true,"access_status",access.ToString(),"rows",rows,"scanned_count",items.Count,
    "ignored_count",other,"unavailable_count",unavailable,"collected_at",DateTimeOffset.UtcNow.ToString("o"))));return 0;
  }catch(Exception e){Console.WriteLine(json.Serialize(D("ok",false,"error",e.GetType().Name+": "+e.Message,"hresult",e.HResult,"permission_requested",false)));return 1;}
 }
}
