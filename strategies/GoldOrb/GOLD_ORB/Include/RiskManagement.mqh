//+------------------------------------------------------------------+
//|                                                 TradeVirtual.mqh |
//|                                              Playground Inc 2021 |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Playground Inc 2021"
#property link      "https://www.mql5.com"
#property version   "1.00"






void MonitorVirtualPostion(VirtualTradeInfo &vTrade)

  {

   int i = 0;
   int total_positions = ArraySize(vTrade.position);
//printf(ArraySize(vTrade.position));


   if(total_positions!=0)
     {

      for(i=0 ; i <= total_positions -1; i++)

        {


         if(vTrade.position[i].type == "long" && SymbolInfoDouble(_Symbol,SYMBOL_BID) >= vTrade.position[i].tp &&vTrade.position[i].tp > 0)

           {

            //update deals
            tradevirtual.TakeProfit(vTrade,i);

            //update close trade
            //Update position
            vTrade.position[i].comment = "closed";
           }

         //Print("Stoploss = ",  vTrade.position[i].sl);
         //Print("Current_Price = ", SymbolInfoDouble(_Symbol,SYMBOL_BID));
         if(vTrade.position[i].type == "long" && SymbolInfoDouble(_Symbol,SYMBOL_BID) <= vTrade.position[i].sl)

           {

            //update deals
            tradevirtual.StopLoss(vTrade,i);

            //update close trade
            //Update position
            vTrade.position[i].comment = "closed";

           }


         if(vTrade.position[i].type == "short" && SymbolInfoDouble(_Symbol,SYMBOL_ASK) <= vTrade.position[i].tp && vTrade.position[i].tp > 0)

           {
            //update deals
            tradevirtual.TakeProfit(vTrade,i);

            //update close trade
            //Update position
            vTrade.position[i].comment = "closed";

           }


         if(vTrade.position[i].type == "short" && SymbolInfoDouble(_Symbol,SYMBOL_ASK) >= vTrade.position[i].sl)

           {

            //update deals
            tradevirtual.StopLoss(vTrade,i);


            //update close trade
            //Update position
            vTrade.position[i].comment = "closed";

           }

        }


      //Update Position container - resizing
      bool position_update = false;
      i=0;
      do

        {
         total_positions = ArraySize(vTrade.position);
         for(i=0 ; i <= total_positions -1; i++)

           {

            if(total_positions ==0)
               position_update = true;

            if(vTrade.position[i].comment == "closed")
              {
               //remove this element on the array
               ArrayRemove(vTrade.position,i,1);


               if(i== total_positions-1)

                 {
                  position_update = true;
                 }

               break;

              }

            if(i== total_positions-1)

              {
               position_update = true;
              }

           }
        }
      while(position_update == false);

     }

  }
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
bool CheckSlope(VirtualTradeInfo &vTrade, int offset_length)
//+------------------------------------------------------------------+
  {


   int deal_size = ArraySize(vTrade.deals);
   int index = deal_size -1;
//int offset_length = 5;

   if(deal_size > offset_length)
     {

      if((vTrade.deals[index].balance - vTrade.deals[index-(offset_length)].balance)>0)

        {


         return(true);
        }
      else
         return(false);
     }

   else
      return(false);

  }
//+------------------------------------------------------------------+
bool LossStreakCounter(VirtualTradeInfo &vTrade, double streak)
  {
   
   int streakLimit = (int)streak;
   int index = ArraySize(vTrade.closetrades)-1;
   double container[];
   ArrayResize(container,streakLimit);
   int i =0;

   
   if(ArraySize(vTrade.closetrades) >= streakLimit)
     {

      for(i = index ; i> index - streakLimit; i--)

        {

         if(vTrade.closetrades[i].result == "WIN")
            container[index - i] = 0;

         if(vTrade.closetrades[i].result == "LOSS")
            container[index - i] = 1;

        }

      if(MathSum(container) == streakLimit)
        {
         return(true);
        }
      else
         return(false);
     }

   else
      return (false);
      
     

  }
//+------------------------------------------------------------------+

