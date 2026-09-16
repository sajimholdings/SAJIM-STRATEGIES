//+------------------------------------------------------------------+
//|                                                  SajimLogger.mqh |
//|                                  Copyright 2026, SAJIM Strategies |
//|                         Universal Strategy Evaluation & Benchmarking|
//+------------------------------------------------------------------+
#property copyright "SAJIM Strategies"
#property link      "https://sajim.com"

//+------------------------------------------------------------------+
//| Harvests all closed deals in account history to sajim_trades.csv  |
//+------------------------------------------------------------------+
void SajimHarvestTrades(string strategyName, string sym, string tf)
{
    if(!HistorySelect(0, TimeCurrent()))
    {
        Print("SajimLogger: Failed to select account history! Error: ", GetLastError());
        return;
    }

    string filename = "sajim_trades.csv";
    
    // First try opening with FILE_COMMON (accessible from Python without navigating tester hashes)
    int fileHandle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
    if(fileHandle == INVALID_HANDLE)
    {
        // Fallback to local sandbox
        fileHandle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
        if(fileHandle == INVALID_HANDLE)
        {
            Print("SajimLogger: Cannot open ", filename, " for writing! Error: ", GetLastError());
            return;
        }
    }

    // Write SAJIM Schema Header
    FileWrite(fileHandle,
        "trade_id",
        "strategy_name",
        "engine",
        "symbol",
        "timeframe",
        "direction",
        "entry_time",
        "entry_price",
        "position_size",
        "exit_time",
        "exit_price",
        "exit_reason",
        "gross_pnl",
        "net_pnl",
        "commission",
        "swap_cost",
        "return_pct",
        "mae",
        "mfe",
        "duration_seconds"
    );

    int totalDeals = HistoryDealsTotal();
    int exportedCount = 0;

    for(int i = 0; i < totalDeals; i++)
    {
        ulong dealTicket = HistoryDealGetTicket(i);
        if(dealTicket == 0) continue;

        long entryType = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);

        // Process only closing deals (DEAL_ENTRY_OUT or DEAL_ENTRY_OUT_BY)
        if(entryType == DEAL_ENTRY_OUT || entryType == DEAL_ENTRY_OUT_BY)
        {
            long posId = HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
            double exitPrice = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
            double volume = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
            double profit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
            double comm = HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
            double swap = HistoryDealGetDouble(dealTicket, DEAL_SWAP);
            datetime exitTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
            string dealSymbol = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
            string comment = HistoryDealGetString(dealTicket, DEAL_COMMENT);

            // Find matching opening deal (DEAL_ENTRY_IN) for this position
            datetime entryTime = exitTime;
            double entryPrice = exitPrice;
            string directionStr = "BUY";

            for(int j = 0; j < i; j++)
            {
                ulong inTicket = HistoryDealGetTicket(j);
                if(HistoryDealGetInteger(inTicket, DEAL_POSITION_ID) == posId &&
                   HistoryDealGetInteger(inTicket, DEAL_ENTRY) == DEAL_ENTRY_IN)
                {
                    entryTime = (datetime)HistoryDealGetInteger(inTicket, DEAL_TIME);
                    entryPrice = HistoryDealGetDouble(inTicket, DEAL_PRICE);
                    ENUM_DEAL_TYPE inType = (ENUM_DEAL_TYPE)HistoryDealGetInteger(inTicket, DEAL_TYPE);
                    comm += HistoryDealGetDouble(inTicket, DEAL_COMMISSION);
                    swap += HistoryDealGetDouble(inTicket, DEAL_SWAP);
                    directionStr = (inType == DEAL_TYPE_BUY) ? "BUY" : "SELL";
                    break;
                }
            }

            // Determine exit reason
            string exitReason = "SIGNAL";
            string lowerComment = comment;
            StringToLower(lowerComment);
            if(StringFind(lowerComment, "sl") >= 0 || StringFind(lowerComment, "stop loss") >= 0)
                exitReason = "SL";
            else if(StringFind(lowerComment, "tp") >= 0 || StringFind(lowerComment, "take profit") >= 0)
                exitReason = "TP";
            else if(StringFind(lowerComment, "so") >= 0 || StringFind(lowerComment, "stop out") >= 0)
                exitReason = "STOP_OUT";

            double netPnl = profit + comm + swap;
            long durationSec = (long)(exitTime - entryTime);

            // Convert MT5 time "YYYY.MM.DD HH:MM:SS" to ISO-8601 "YYYY-MM-DDTHH:MM:SSZ"
            string sEntry = TimeToString(entryTime, TIME_DATE | TIME_SECONDS);
            StringReplace(sEntry, ".", "-");
            StringReplace(sEntry, " ", "T");
            sEntry = sEntry + "Z";

            string sExit = TimeToString(exitTime, TIME_DATE | TIME_SECONDS);
            StringReplace(sExit, ".", "-");
            StringReplace(sExit, " ", "T");
            sExit = sExit + "Z";

            FileWrite(fileHandle,
                "MT5_" + IntegerToString(dealTicket),
                strategyName,
                "mt5:headless",
                (dealSymbol != "" ? dealSymbol : sym),
                tf,
                directionStr,
                sEntry,
                DoubleToString(entryPrice, 5),
                DoubleToString(volume, 2),
                sExit,
                DoubleToString(exitPrice, 5),
                exitReason,
                DoubleToString(profit, 2),
                DoubleToString(netPnl, 2),
                DoubleToString(comm, 2),
                DoubleToString(swap, 2),
                "0.0", // return_pct
                "",    // mae
                "",    // mfe
                IntegerToString(durationSec)
            );
            exportedCount++;
        }
    }

    FileClose(fileHandle);
    Print("SajimLogger: Successfully exported ", exportedCount, " closed trades to ", filename);
}
