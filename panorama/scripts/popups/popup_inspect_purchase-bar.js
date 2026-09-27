'use-strict';

var InpsectPurchaseBar=(function()
{
	var m_itemid='';
	var m_storeItemid='';
	var m_elPanel=null;
	var m_showToolUpsell=false;
	var m_isXrayMode=false;
	var m_allowXrayPurchase=false;
	var m_bOverridePurchaseMultiple=false;
	var m_blurOperationPanel=false;
	var m_isRevivalOperationPass=false;

	var _Init=function(elPanel,itemId,get)
	{
		m_storeItemid=get("storeitemid","");
		m_bOverridePurchaseMultiple=get("overridepurchasemultiple","")==='1';
		m_blurOperationPanel=$.GetContextPanel().GetAttributeString('bluroperationpanel','false')==='true';
		m_itemid=m_storeItemid?m_storeItemid:itemId;
		m_isRevivalOperationPass=get('revivalpass','false')==='true';

		var faux=InventoryAPI.IsFauxItemID(m_itemid);
		var price=faux?ItemInfo.GetStoreOriginalPrice(m_itemid,1):'';
		if((!price&&!m_isRevivalOperationPass)||get('inspectonly','false')==='true'||!InventoryAPI.IsValidItemID(m_itemid))
		{
			elPanel.AddClass('hidden');
			return;
		}

		m_elPanel=elPanel;
		m_isXrayMode=get("isxraymode","no")==='yes';
		m_allowXrayPurchase=get("allowxraypurchase","no")==='yes';
		m_showToolUpsell=get("toolid",'')==='';
		elPanel.RemoveClass('hidden');
		_SetPurchaseImage(elPanel,itemId);
		_SetDialogVariables(elPanel,m_itemid);
		_UpdateDecString(elPanel);
		_SetUpPurchaseBtn(elPanel);
		_UpdatePurchasePrice();
	};

	var _SetDialogVariables=function(elPanel,itemId)
	{
		elPanel.SetDialogVariable("itemname",ItemInfo.GetName(itemId));
	};

	var _SetPurchaseImage=function(elPanel,itemId)
	{
		var el=elPanel.FindChildInLayoutFile('PurchaseItemImage');
		el.itemid=itemId;
		el.SetHasClass('popup-capability-faded',m_isXrayMode&&!m_allowXrayPurchase);
	};

	var _UpdateDecString=function(elPanel)
	{
		var el=m_elPanel.FindChildInLayoutFile('PurchaseItemName');
		if(m_isXrayMode)
		{
			elPanel.SetDialogVariable("itemprice",ItemInfo.GetStoreSalePrice(m_itemid,1));
			el.text="#popup_capability_upsell_xray";
		}
		else if(!m_storeItemid&&m_showToolUpsell)
			el.text="#popup_capability_upsell";
		else
			el.text="#popup_capability_use";
		el.SetHasClass('popup-capability-faded',m_isXrayMode&&!m_allowXrayPurchase);
	};

	var _UpdatePurchasePrice=function()
	{
		if(!m_elPanel.IsValid())return;
		var btn=m_elPanel.FindChildInLayoutFile('PurchaseBtn');
		var dd=m_elPanel.FindChildInLayoutFile('PurchaseCountDropdown');
		if(m_isRevivalOperationPass)
		{
			dd.visible=false;
			btn.text='GET PASS';
			_UpdateSalePrice('');
			return;
		}

		var qty=1;
		var multi=!m_isXrayMode&&_isAllowedToPurchaseMultiple();
		dd.visible=multi;
		if(multi)qty=Number(dd.GetSelected().id);
		btn.text=m_isXrayMode?'#popup_totool_purchase_header':ItemInfo.GetStoreSalePrice(m_itemid,qty);
		_UpdateSalePrice(ItemInfo.GetStoreOriginalPrice(m_itemid,qty));
	};

	var _isAllowedToPurchaseMultiple=function()
	{
		if(m_bOverridePurchaseMultiple)return true;
		if(InventoryAPI.GetItemAttributeValue(m_itemid,'season access'))return false;
		return InventoryAPI.GetItemDefinitionName(m_itemid)!=='casket';
	};

	var _SetUpPurchaseBtn=function(elPanel)
	{
		var btn=elPanel.FindChildInLayoutFile('PurchaseBtn');
		btn.enabled=!m_isXrayMode||m_allowXrayPurchase;
		btn.SetPanelEvent('onactivate',_OnActivate);
	};

	var _UpdateSalePrice=function(price)
	{
		var sale=m_elPanel.FindChildInLayoutFile('PurchaseSalePrice');
		var percent=m_elPanel.FindChildInLayoutFile('PurchaseItemPercent');
		var reduction=ItemInfo.GetStoreSalePercentReduction(m_itemid);
		if(reduction)
		{
			sale.visible=true;
			sale.text=price;
			percent.visible=true;
			percent.text=reduction;
			return;
		}
		sale.visible=false;
		percent.visible=false;
	};

	var _OnDropdownUpdate=function(){_UpdatePurchasePrice();};

	var _OnActivate=function()
	{
		if(m_isRevivalOperationPass)
		{
			GameInterfaceAPI.ConsoleCommand('con_logfile "revival_operation_pass_buy.log"; echo REVIVAL_OPERATION_PASS_BUY_V1; con_logfile ""');
			$.Schedule(0.75,_ClosePopup);
			return;
		}

		var dd=m_elPanel.FindChildInLayoutFile('PurchaseCountDropdown');
		var qty=Number(dd.GetSelected().id);
		var defName=ItemInfo.GetItemDefinitionName(m_itemid);
		var list=[];
		for(var i=0;i<qty;i++)list.push(m_itemid);
		var purchase=list.join(',');

		if(defName&&defName.startsWith('coupon - crate_patch_')&&!ItemInfo.FindAnyUserOwnedCharacterItemID())
		{
			UiToolkitAPI.ShowGenericPopupYesNo(
				$.Localize('#CSGO_Patch_NoAgent_Title'),
				$.Localize('#CSGO_Patch_NoAgent_Message'),
				'',
				function(){ItemInfo.ItemPurchase(purchase);},
				function(){}
			);
		}
		else ItemInfo.ItemPurchase(purchase);
	};

	var _ClosePopup=function()
	{
		InventoryAPI.StopItemPreviewMusic();
		if(m_blurOperationPanel)$.DispatchEvent('UnblurOperationPanel');
		$.DispatchEvent('HideSelectItemForCapabilityPopup');
		$.DispatchEvent('UIPopupButtonClicked','');
		$.DispatchEvent('CapabilityPopupIsOpen',false);
	};

	return{Init:_Init,OnDropdownUpdate:_OnDropdownUpdate,ClosePopup:_ClosePopup};
})();

(function(){})();
