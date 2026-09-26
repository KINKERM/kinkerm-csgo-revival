'use strict';

var HudMissionPanel = ( function() {

	var _m_cp = $.GetContextPanel();
	var _m_missionId = undefined;
	var _m_elMission = null;

	// Exact clicked quest is carried through a process-local Source cvar.
	var _GetRevivalActiveQuestID = function()
	{
		var q = parseInt( GameStateAPI.GetActiveQuestID() ) || 0;
		if( q ) return q;
		var s = GameInterfaceAPI.GetSettingString( 'con_filter_text_out' ) || '';
		if( s.indexOf( 'REVIVALQ:' ) === 0 ) q = parseInt( s.substr( 9 ) ) || 0;
		$.Msg( '[revival operation hud] cvar raw=' + s + ' quest=' + q );
		return q;
	}

	var _OnMatchStart = function()
	{
		                                                                      	
		if ( GameStateAPI.GetGameModeInternalName( false ) === "survival" )
		{
			_HideMissionPanel();
			return;
		}

		$.Schedule( 1, _UpdateMission );

		                                                                                
		                         
		if( isCompetitiveNotSurvival() )
		{
			$.Schedule( 5, _HideMissionPanel );
		}
	}

	var _UpdateProgress = function()
	{
		$.Schedule( 0, _UpdateMission );
	}

	var _UpdateMission = function()
	{
		_m_missionId = _GetRevivalActiveQuestID();
		$.Msg( '[revival operation hud] update=' + _m_missionId );
		if( !_m_missionId || _m_missionId === 0 || _m_missionId === '0' || GameStateAPI.GetMapBSPName() === 'lobby_mapveto' )
		{
			_DeleteMissionPanel();
			return;
		}

		var oMissionDetails = OperationUtil.GetMissionDetails( _m_missionId );
		                                        
		if( oMissionDetails.isReplayable )
		{
			_DeleteMissionPanel();
			return;
		}

		_m_elMission = OperationMission.CreateMission( 
			_m_cp, 
			oMissionDetails,
			false );

		var isunlocked = true;
		var elMissionCard = null;
		var showCompleteWarning = false;
		var isList = oMissionDetails.missonType === 'sequential' || oMissionDetails.missonType === 'checklist';
		OperationMission.UpdateMissionDisplay( _m_elMission, oMissionDetails, isunlocked, elMissionCard, isList);

		if( isList )
		{
			var aIncomplete = oMissionDetails.aSubQuests.filter( element => ( element.nUncommitted < 1 ) && ( element.nsubQuestPointsRemaining > 0 ));

			OperationMission.HudIncompleteSubMissions( 
				_m_elMission,
				aIncomplete
			);

			showCompleteWarning = aIncomplete.length === 0;
		}
		else if( oMissionDetails.missonType === 'or' )
		{
			oMissionDetails.aSubQuests.forEach( element => {
				if(( ( element.nUncommitted + element.nEarned ) === element.nGoal))
				{
					showCompleteWarning = true;
				}
			});
		}

		_m_cp.SetHasClass( 'show', true );
		_m_cp.SetHasClass( 'show-uncommitted-warning', showCompleteWarning );
		_m_cp.SetHasClass( 'short', oMissionDetails.missonType === 'sequential' );
	}

	var _DeleteMissionPanel = function()
	{
		                                                                             
		var _m_elMission = _m_cp.FindChildInLayoutFile( _m_missionId );
		if( _m_elMission && _m_elMission.IsValid() )
		{
			_m_elMission.DeleteAsync( 0.0 );
		}
		_HideMissionPanel();
	}

	var _HideMissionPanel = function()
	{
		_m_cp.SetHasClass( 'show', false );
	}

	var _OnReceiveMVP = function()
	{
		if( _m_cp.BHasClass( 'show') === false )
		{
			_UpdateMission();
		}
		$.Schedule( 2.5, _UpdateMission );
	};

	var _OnRoundFreezeTimeEnd = function()
	{
		                                             
		if( isCompetitiveNotSurvival() )
		{
			_HideMissionPanel();
		}
	};

	var isCompetitiveNotSurvival = function()
	{
		                                                                          
		return GameStateAPI.IsQueuedMatchmaking() && GameStateAPI.GetGameModeInternalName( false ) !== "survival";
	};

	var _SurvivalMatchStart = function( SurvivalPhase )
	{
		if ( SurvivalPhase === 5 )
		{
			_UpdateMission();
		}
	};

	return {
		OnMatchStart: _OnMatchStart,
		LevelTransitionStart: _DeleteMissionPanel,
		UpdateProgress: _UpdateProgress,
		OnReceiveMVP: _OnReceiveMVP,
		OnRoundFreezeTimeEnd: _OnRoundFreezeTimeEnd,
		SurvivalMatchStart: _SurvivalMatchStart
	};
} )();

(function()
{
	$.Msg( '[revival operation hud] script loaded' );
	// Revival direct-connect reservations do not reliably emit GameState_OnMatchStart.
	// GameState_ServerSpawn is dispatched on every successful server connection.
	$.RegisterForUnhandledEvent( "GameState_ServerSpawn", HudMissionPanel.OnMatchStart );
	$.RegisterForUnhandledEvent( "GameState_LevelInitPreEntity", HudMissionPanel.LevelTransitionStart );
	$.RegisterForUnhandledEvent( "OnQuestProgressMade", HudMissionPanel.UpdateProgress );
	$.RegisterForUnhandledEvent( 'OnRoundMVPShown', HudMissionPanel.OnReceiveMVP );
	$.RegisterForUnhandledEvent( 'OnRoundFreezeTimeEnd', HudMissionPanel.OnRoundFreezeTimeEnd );
	$.RegisterForUnhandledEvent( 'SurvivalSpawnSelectModeChange', HudMissionPanel.SurvivalMatchStart );
})();

                                                                                                               
                                                                                                                         