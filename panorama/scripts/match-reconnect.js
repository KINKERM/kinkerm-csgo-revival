var MatchmakingReconnect=(function(){
var p=$.GetContextPanel(),a=false;
var u=function(){
var h=CompetitiveMatchAPI.HasOngoingMatch();
p.SetHasClass('hidden',!h);
if(!h){a=false;return;}
if(!a){a=true;$.Msg('REVIVAL_PANORAMA_AUTO_RECONNECT_V1');CompetitiveMatchAPI.ActionReconnectToOngoingMatch();}
};
var i=function(){
u();
var r=p.FindChildInLayoutFile('MatchmakingReconnect');
r.SetPanelEvent('onactivate',function(){CompetitiveMatchAPI.ActionReconnectToOngoingMatch();$.DispatchEvent('PlaySoundEffect','UIPanorama.generic_button_press','MOUSE');});
var b=p.FindChildInLayoutFile('MatchmakingAbandon');
b.SetPanelEvent('onactivate',function(){CompetitiveMatchAPI.ActionAbandonOngoingMatch();$.DispatchEvent('PlaySoundEffect','UIPanorama.generic_button_press','MOUSE');});
p.OnPropertyTransitionEndEvent=function(n,x){if(p.id===n&&x==='opacity'&&p.visible===true&&p.BIsTransparent()){p.visible=false;return true;}return false;};
};
return{Init:i,UpdateState:u};
})();
(function(){MatchmakingReconnect.Init();$.RegisterForUnhandledEvent("PanoramaComponent_Lobby_MatchmakingSessionUpdate",MatchmakingReconnect.UpdateState);$.RegisterForUnhandledEvent('PanoramaComponent_GC_Hello',MatchmakingReconnect.UpdateState);})();
