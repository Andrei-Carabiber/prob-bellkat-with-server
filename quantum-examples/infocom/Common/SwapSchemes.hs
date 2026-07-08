{- |
    Contains the description of the swapping schemes on the path A-X-Y-Z-E considered in the paper
-}

module Common.SwapSchemes
    ( defaultProtocolName
    , defaultEventName
    , availableProtocols
    , availableEvents
    , selectProtocol
    , selectEvent
    , networkBounds
    , swapActionConfig
    , runSwapScheme
    , runSwapSchemeWithBounds
    , runSwapSchemeWithActionConfigAndBounds
    ) where

import BellKAT.QuantumPrelude hiding (lookup)
import Data.List (intercalate)
import qualified Common.NetworkConfig as Net
import System.Environment (withArgs)

defaultProtocolName :: String
defaultProtocolName = "swap-asap"

defaultEventName :: String
defaultEventName = "pure"

pGen :: QBKATPolicy
pGen =
            createIf
                ("A" /~? "X" &&* "A" /~? "Y" &&* "A" /~? "Z")
                ("A", "X")
        <||>
            createIf
                ("X" /~? "Y" &&* "A" /~? "Y" &&* "X" /~? "Z" &&* "A" /~? "Z" &&* "X" /~? "E")
                ("X", "Y")
        <||>
            createIf
                ("Y" /~? "Z" &&* "X" /~? "Z" &&* "Y" /~? "E" &&* "A" /~? "Z" &&* "X" /~? "E")
                ("Y", "Z")
        <||>
            createIf
                ("Z" /~? "E" &&* "Y" /~? "E" &&* "X" /~? "E")
                ("Z", "E")

createIf :: QBKATTest -> (Location, Location) -> QBKATPolicy
createIf guard edge = ite guard (ucreate edge) mempty

pSwapASAP :: QBKATPolicy
pSwapASAP =
        sswap ["X", "Y", "Z"] ("A", "E")
        <.>
        (
                sswap ["X", "Z"] ("A", "E")
            <||>
                sswap ["X", "Y"] ("A", "E")
            <||>
                sswap ["Y", "Z"] ("A", "E")
            <||>
                sswap ["X", "Y"] ("A", "Z")
            <||>
                sswap ["Y", "Z"] ("X", "E")
        )
        <.>
        (
                swap "X" ("A", "E")
            <||>
                swap "Y" ("A", "E")
            <||>
                swap "Z" ("A", "E")
            <||>
                swap "X" ("A", "Z")
            <||>
                swap "Y" ("A", "Z")
            <||>
                swap "Z" ("X", "E")
            <||>
                swap "Y" ("X", "E")
            <||>
                swap "X" ("A", "Y")
            <||>
                swap "Y" ("X", "Z")
            <||>
                swap "Z" ("Y", "E")
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
        swap "X" ("A", "Y")
        <>
        swap "Y" ("A", "Z")
        <>
        swap "Z" ("A", "E")
    )

pSeq2 :: QBKATPolicy
pSeq2 = while ("A" /~? "E")
    (
        pGen
        <>
        swap "Z" ("Y", "E")
        <>
        swap "Y" ("X", "E")
        <>
        swap "X" ("A", "E")
    )

pDoubling :: QBKATPolicy
pDoubling = while ("A" /~? "E")
    (
        pGen
        <>
        swap "X" ("A", "Y")
        <>
        swap "Z" ("Y", "E")
        <>
        swap "Y" ("A", "E")
    )

protocols :: [(String, QBKATPolicy)]
protocols =
    [ ("swap-asap", pASAP)
    , ("asap", pASAP)
    , ("left-to-right", pSeq1)
    , ("right-to-left", pSeq2)
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

networkBounds :: NetworkBounds QBKATTag
networkBounds = Net.networkBoundsFor (Net.physicalTopologyLinks <> Net.pathPairs)

swapActionConfig :: Net.NetworkParameters -> ProbabilisticActionConfiguration
swapActionConfig parameters =
    Net.actionConfigFor parameters Net.pathElementaryLinks ["X", "Y", "Z"]

runSwapScheme :: Net.NetworkParameters -> String -> String -> [String] -> IO ()
runSwapScheme parameters =
    runSwapSchemeWithBounds parameters networkBounds

runSwapSchemeWithBounds
    :: Net.NetworkParameters
    -> NetworkBounds QBKATTag
    -> String
    -> String
    -> [String]
    -> IO ()
runSwapSchemeWithBounds parameters =
    runSwapSchemeWithActionConfigAndBounds (swapActionConfig parameters)

runSwapSchemeWithActionConfigAndBounds
    :: ProbabilisticActionConfiguration
    -> NetworkBounds QBKATTag
    -> String
    -> String
    -> [String]
    -> IO ()
runSwapSchemeWithActionConfigAndBounds actionConfig bounds protocolName eventName qbkatArgs = do
    protocol <- either fail pure (selectProtocol protocolName)
    ev <- either fail pure (selectEvent eventName)
    withArgs qbkatArgs $
        qbkatMainD actionConfig bounds ev protocol mempty
