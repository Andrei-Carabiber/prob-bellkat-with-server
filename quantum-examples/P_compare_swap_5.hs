import BellKAT.QuantumPrelude hiding (lookup)
import Data.List (intercalate, stripPrefix)
import System.Environment (getArgs, withArgs)

--  | Create a link X~Y if X and Y are not already connected
pGen :: QBKATPolicy
pGen =
            ite
                ("A" /~? "B" &&* "A" /~? "C" &&* "A" /~? "D")
                (ucreate ("A", "B"))
                mempty
        <||>
            ite
                ("B" /~? "C" &&* "A" /~? "C" &&* "B" /~? "D" &&* "A" /~? "D" &&* "B" /~? "E")
                (ucreate ("B", "C"))
                mempty
        <||>
            ite
                ("C" /~? "D" &&* "B" /~? "D" &&* "C" /~? "E" &&* "A" /~? "D" &&* "B" /~? "E")
                (ucreate ("C", "D"))
                mempty
        <||>
            ite
                ("D" /~? "E" &&* "C" /~? "E" &&* "B" /~? "E")
                (ucreate ("D", "E"))
                mempty

pSwapASAP :: QBKATPolicy
pSwapASAP =
        -- if you have all links, swap all simultaneously
        sswap ["B", "C", "D"] ("A", "E")
        <.>
        -- otherwise, try swapping three links into one
        (
                sswap ["B", "D"] ("A", "E")
            <||>
                sswap ["B", "C"] ("A", "E")
            <||>
                sswap ["C", "D"] ("A", "E")
            <||>
                sswap ["B", "C"] ("A", "D")
            <||>
                sswap ["C", "D"] ("B", "E")
        )
        <.>
        -- otherwise, try swapping two links into one
        (
                swap "B" ("A", "E")
            <||>
                swap "C" ("A", "E")
            <||>
                swap "D" ("A", "E")
            <||>
                swap "B" ("A", "D")
            <||>
                swap "C" ("A", "D")
            <||>
                swap "D" ("B", "E")
            <||>
                swap "C" ("B", "E")
            <||>
                swap "B" ("A", "C")
            <||>
                swap "C" ("B", "D")
            <||>
                swap "D" ("C", "E")
        )

pASAP :: QBKATPolicy
pASAP = while ("A" /~? "E")
    (
        pGen
        <>
        pSwapASAP
        <>
        pSwapASAP
        <>
        pSwapASAP
    )

pSeq1 :: QBKATPolicy
pSeq1 = while ("A" /~? "E")
    (
        pGen
        <>
        swap "B" ("A", "C")
        <>
        swap "C" ("A", "D")
        <>
        swap "D" ("A", "E")
    )

pSeq2 :: QBKATPolicy
pSeq2 = while ("A" /~? "E")
    (
        pGen
        <>
        swap "D" ("C", "E")
        <>
        swap "C" ("B", "E")
        <>
        swap "B" ("A", "E")
    )

pSim :: QBKATPolicy
pSim = while ("A" /~? "E")
    (
        pGen
        <>
        sswap ["B", "C", "D"] ("A", "E")
    )

pDoubling :: QBKATPolicy
pDoubling = while ("A" /~? "E")
    (
        pGen
        <>
        swap "B" ("A", "C")
        <>
        swap "D" ("C", "E")
        <>
        swap "C" ("A", "E")
    )

protocols :: [(String, QBKATPolicy)]
protocols =
    [ ("asap", pASAP)
    , ("left-to-right", pSeq1)
    , ("right-to-left", pSeq2)
    , ("at-last", pSim)
    , ("doubling", pDoubling)
    ]

events :: [(String, QBKATTest)]
events =
    [ ("static", "A" ~~? "E")
    , ("pure", "A" -~? "E")
    , ("mixed", "A" =~? "E")
    ]

selectProtocol :: String -> Either String QBKATPolicy
selectProtocol name =
    maybe
        (Left $ "Unknown protocol '" <> name <> "'. Available protocols: " <> availableProtocols)
        Right
        (lookup name protocols)

selectEvent :: String -> Either String QBKATTest
selectEvent name =
    maybe
        (Left $ "Unknown event '" <> name <> "'. Available events: " <> availableEvents)
        Right
        (lookup name events)

availableProtocols :: String
availableProtocols = intercalate ", " (fmap fst protocols)

availableEvents :: String
availableEvents = intercalate ", " (fmap fst events)

stripExampleArgs :: [String] -> Either String (String, String, [String])
stripExampleArgs = go "asap" "pure" []
  where
    go selectedProtocol selectedEvent kept [] = Right (selectedProtocol, selectedEvent, reverse kept)
    go _ _ _ ["--protocol"] = Left "Missing value for --protocol."
    go _ _ _ ["--event"] = Left "Missing value for --event."
    go _ selectedEvent kept ("--protocol" : name : rest) = go name selectedEvent kept rest
    go selectedProtocol _ kept ("--event" : name : rest) = go selectedProtocol name kept rest
    go selectedProtocol selectedEvent kept (arg : rest)
        | Just name <- stripPrefix "--protocol=" arg = go name selectedEvent kept rest
        | Just name <- stripPrefix "--event=" arg = go selectedProtocol name kept rest
        | otherwise = go selectedProtocol selectedEvent (arg : kept) rest

networkCapacity :: NetworkCapacity QBKATTag
networkCapacity =
    [ "A" ~ "B"
    , "B" ~ "C"
    , "C" ~ "D"
    , "D" ~ "E"
    , "A" ~ "C"
    , "B" ~ "D"
    , "C" ~ "E"
    , "A" ~ "D"
    , "B" ~ "E"
    , "A" ~ "E"
    ]

nb :: NetworkBounds QBKATTag
nb = def { nbCapacity = Just networkCapacity }

actionConfig :: Double -> Int -> ProbabilisticActionConfiguration
actionConfig w0 tCoh = PAC
    { pacTransmitProbability = []
    , pacCreateProbability = []
    , pacCreateWerner = []
    , pacUCreateProbability =
        [ (("A", "B"), 1/50)
        , (("B", "C"), 1/50)
        , (("C", "D"), 1/50)
        , (("D", "E"), 1/50)
        ]
    , pacUCreateWerner =
        [ (("A", "B"), w0)
        , (("B", "C"), w0)
        , (("C", "D"), w0)
        , (("D", "E"), w0)
        ]
    , pacSwapProbability = [("B", 1/2), ("C", 1/2), ("D", 1/2)]
    , pacCoherenceTime = [("A", tCoh), ("B", tCoh), ("C", tCoh), ("D", tCoh), ("E", tCoh)]
    , pacDistances =
        [ (("A", "B"), 1)
        , (("B", "C"), 1)
        , (("C", "D"), 1)
        , (("D", "E"), 1)
        , (("A", "C"), 2)
        , (("B", "D"), 2)
        , (("C", "E"), 2)
        , (("A", "D"), 3)
        , (("B", "E"), 3)
        , (("A", "E"), 4)
        ]
    }

main :: IO ()
main = do
    args <- getArgs
    (protocolName, eventName, qbkatArgs) <-
        either fail pure (stripExampleArgs args)
    protocol <- either fail pure (selectProtocol protocolName)
    ev <- either fail pure (selectEvent eventName)
    let w0  = 98/100
        tCoh = 5000
    withArgs qbkatArgs $
        qbkatMainD (actionConfig w0 tCoh) nb ev protocol mempty
