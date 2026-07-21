import BellKAT.QuantumPrelude

hubFull :: QBKATTest
hubFull =
        hasSubset ["A" ~ "H", "B" ~ "H", "C" ~ "H"]
    ||* hasSubset ["A" ~ "H", "C" ~ "H", "C" ~ "H"]
    ||* hasSubset ["B" ~ "H", "C" ~ "H", "C" ~ "H"]

hubHasRoom :: QBKATTest
hubHasRoom = notB hubFull

aHasRoom :: QBKATTest
aHasRoom = "A" /~? "H" &&* "A" /~? "B"

bHasRoom :: QBKATTest
bHasRoom = "B" /~? "H" &&* "A" /~? "B"

cHasRoom :: QBKATTest
cHasRoom = hasNotSubset ["C" ~ "H", "C" ~ "H"]

allElementaryLinks :: QBKATTest
allElementaryLinks = hasSubset ["A" ~ "H", "B" ~ "H", "C" ~ "H"]

p :: QBKATPolicy
p =
    while ("A" /~? "C" &&* "B" /~? "C" &&* "A" /~? "B")
        (   (   ite (hubHasRoom &&* aHasRoom) (ucreate ("A", "H")) mempty
            <||>
                ite (hubHasRoom &&* bHasRoom) (ucreate ("B", "H")) mempty
            <||>
                ite (hubHasRoom &&* cHasRoom) (ucreate ("C", "H")) mempty
            )
        <>
            (   swap "H" ("A", "C")
            <||>
                swap "H" ("B", "C")
            <||>
                ite allElementaryLinks (swap "H" ("A", "B")) mempty
            )
        )

networkCapacity :: NetworkCapacity QBKATTag
networkCapacity =
    [ "A" ~ "H"
    , "B" ~ "H"
    , "C" ~ "H"
    , "C" ~ "H"
    , "A" ~ "B"
    , "A" ~ "C"
    , "B" ~ "C"
    ]

nb :: NetworkBounds QBKATTag
nb = def
    { nbCapacity = Just networkCapacity
    , nbOperationTiming = InstantaneousOps
    }

actionConfig :: ProbabilisticActionConfiguration
actionConfig =
    PAC
        { pacTransmitProbability = []
        , pacCreateProbability = []
        , pacCreateWerner = []
        , pacUCreateProbability =
            [ (("A", "H"), 1/4)
            , (("B", "H"), 1/3)
            , (("C", "H"), 1/3)
            ]
        , pacSwapProbability =
            [ ("H", 1/2) ]
        , pacUCreateWerner =
            [ (("A", "H"), 90/100)
            , (("B", "H"), 95/100)
            , (("C", "H"), 95/100)
            ]
        , pacCoherenceTime =
            [ ("A", 100)
            , ("B", 100)
            , ("C", 100)
            , ("H", 200)
            ]
        , pacDistances =
            [ (("A", "H"), 1)
            , (("B", "H"), 1)
            , (("C", "H"), 1)
            ]
        }

main :: IO ()
main =
    let ev = "A" ~~? "C" ||* "B" ~~? "C"
    in qbkatMainD actionConfig nb ev p mempty
